"""Device tokens for agent-notes: the machine proves itself to the MCP server.

An enrolled machine holds an ES256 private key (macOS Keychain, or a mode-600 file) and signs a
short-lived token for each MCP connection; the server checks it against the public keys in
AGENT_NOTES_DEVICE_KEYS and gives the session the trusted tier without an elevation. Used by
`mcp-device-header` (Claude Code's headersHelper) and `enroll-device`.

The private key never touches a command line or stdout: it moves between processes on stdin and
on a pipe (/dev/fd/N). Standard library and the system `openssl` only.

Config: the [device] table of ~/.config/agent-notes/hooks.toml, or env:
  AGENT_NOTES_DEVICE_KID      the key id the server knows this machine by (default: hostname)
  AGENT_NOTES_DEVICE_KEY      "keychain" (macOS, default there) or "file:<path>"
  AGENT_NOTES_DEVICE_SERVERS  JSON {"https://notes-mcp.example.com": "agent-notes", ...}:
                              which servers get a token, and the audience for each (the
                              server's AGENT_NOTES_NAME). Any other server gets no header.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import socket
import stat
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

LIFETIME = 12 * 3600  # under the server's 24 h cap; a new one per connection
KEYCHAIN_SERVICE = "agent-notes-device-key"
KID = re.compile(r"^[a-z0-9-]{1,32}$")


@dataclass
class DeviceConfig:
    kid: str
    key: str  # "keychain" or "file:<path>"
    servers: dict[str, str] = field(default_factory=dict)  # origin -> audience
    keychain_service: str = KEYCHAIN_SERVICE

    @classmethod
    def load(cls, env: dict[str, str] | None = None, path: Path | None = None) -> DeviceConfig:
        env = dict(os.environ if env is None else env)
        raw: dict = {}
        path = path or Path(env.get("AGENT_NOTES_HOOKS_CONFIG") or Path.home() / ".config/agent-notes/hooks.toml")
        if path.is_file():
            raw = tomllib.loads(path.read_text()).get("device", {})
        default_kid = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().split(".")[0].lower())[:32] or "device"
        kid = env.get("AGENT_NOTES_DEVICE_KID") or str(raw.get("kid", default_kid))
        default_key = "keychain" if platform.system() == "Darwin" else "file:~/.config/agent-notes/device.key"
        key = env.get("AGENT_NOTES_DEVICE_KEY") or str(raw.get("key", default_key))
        servers = json.loads(env["AGENT_NOTES_DEVICE_SERVERS"]) if env.get("AGENT_NOTES_DEVICE_SERVERS") \
            else dict(raw.get("servers", {}))
        if not KID.match(kid):
            raise ValueError(f"device kid {kid!r}: lowercase letters, digits and - (max 32)")
        if key != "keychain" and not key.startswith("file:"):
            raise ValueError('device key: "keychain" or "file:<path>"')
        return cls(kid=kid, key=key, servers={origin(u): str(a) for u, a in servers.items()},
                   keychain_service=str(raw.get("keychain_service", KEYCHAIN_SERVICE)))

    @property
    def key_file(self) -> Path:
        return Path(os.path.expanduser(self.key[len("file:"):]))

    def audience_for(self, server_url: str) -> str | None:
        """The audience for this server, or None: only configured servers get a token."""
        return self.servers.get(origin(server_url)) if server_url else None


def origin(url: str) -> str:
    u = urlsplit(url.strip())
    if u.scheme not in ("https", "http") or not u.hostname:
        return ""
    port = f":{u.port}" if u.port and u.port != {"https": 443, "http": 80}[u.scheme] else ""
    return f"{u.scheme}://{u.hostname.lower()}{port}"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw(sig: bytes) -> bytes:
    """ECDSA DER (SEQUENCE of two INTEGERs) to JOSE's fixed 64-byte r||s."""
    def read_int(buf: bytes, i: int) -> tuple[int, int]:
        if buf[i] != 0x02:
            raise ValueError("not a DER INTEGER")
        n = buf[i + 1]
        return int.from_bytes(buf[i + 2:i + 2 + n], "big"), i + 2 + n
    i = 2 if sig[1] < 0x80 else 3
    r, i = read_int(sig, i)
    s, _ = read_int(sig, i)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def pem_from_der_b64(b64: str) -> bytes:
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\n".encode()


def der_b64_from_pem(pem: bytes) -> str:
    lines = [l for l in pem.decode().splitlines() if l and not l.startswith("-----")]
    return "".join(lines)


# --- key storage ---------------------------------------------------------------------------

def check_private_file(f: Path) -> None:
    st = f.stat()
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{f} is readable by others: chmod 600 it")
    if st.st_uid != os.getuid():
        raise PermissionError(f"{f} belongs to another user")


def load_key(cfg: DeviceConfig) -> bytes:
    """The private key as PKCS#8 PEM (LibreSSL on macOS only reads PEM in `dgst -sign`)."""
    if cfg.key == "keychain":
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", cfg.keychain_service,
                            "-a", cfg.kid, "-w"], capture_output=True, text=True, check=True)
        return pem_from_der_b64(r.stdout.strip())
    f = cfg.key_file
    check_private_file(f)
    return f.read_bytes()


def store_key(cfg: DeviceConfig, pem: bytes) -> None:
    if cfg.key == "keychain":
        # `security -i` reads its command from stdin: the key never appears in a process list.
        cmd = f"add-generic-password -U -s {cfg.keychain_service} -a {cfg.kid} -w {der_b64_from_pem(pem)}\n"
        r = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True)
        if r.returncode != 0 or "error" in (r.stdout + r.stderr).lower():
            raise RuntimeError("could not store the key in the Keychain")
        return
    f = cfg.key_file
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite a key
    with os.fdopen(fd, "wb") as out:
        out.write(pem)


def key_exists(cfg: DeviceConfig) -> bool:
    if cfg.key == "keychain":
        return subprocess.run(["/usr/bin/security", "find-generic-password", "-s", cfg.keychain_service,
                               "-a", cfg.kid], capture_output=True).returncode == 0
    return cfg.key_file.exists()


# --- openssl ---------------------------------------------------------------------------------

def _openssl_with_key(args: list[str], pem: bytes, data: bytes = b"") -> bytes:
    """Run openssl with the key on a pipe (/dev/fd/N) and `data` on stdin."""
    r, w = os.pipe()
    try:
        with subprocess.Popen(["openssl", *args, f"/dev/fd/{r}"], pass_fds=(r,), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            os.close(r)
            r = -1
            os.write(w, pem)
            os.close(w)
            w = -1
            out, err = p.communicate(data)
    finally:
        for fd in (r, w):
            if fd >= 0:
                os.close(fd)
    if p.returncode != 0:
        raise RuntimeError(f"openssl {args[0]} failed: {err.decode()[:200]}")
    return out


def generate_key() -> bytes:
    ec = subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout"],
                        capture_output=True, check=True).stdout
    return subprocess.run(["openssl", "pkcs8", "-topk8", "-nocrypt"], input=ec,
                          capture_output=True, check=True).stdout


def public_pem(pem: bytes) -> str:
    r = subprocess.run(["openssl", "ec", "-pubout"], input=pem, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("openssl could not derive the public key")
    return r.stdout.decode()


def sign_token(pem: bytes, kid: str, audience: str, now: int | None = None, lifetime: int = LIFETIME) -> str:
    now = int(time.time()) if now is None else now
    head = b64url(json.dumps({"alg": "ES256", "typ": "JWT", "kid": kid}, separators=(",", ":")).encode())
    body = b64url(json.dumps({"aud": audience, "iat": now, "exp": now + lifetime}, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}"
    sig = _openssl_with_key(["dgst", "-sha256", "-sign"], pem, signing_input.encode())
    return f"{signing_input}.{b64url(der_to_raw(sig))}"
