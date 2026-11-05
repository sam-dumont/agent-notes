"""Render CouchDB's ini and the LiveSync daemon's settings from env, for Docker
Compose (a one-shot `render` service before CouchDB and the daemon start).
deploy/terraform renders the same template with templatefile(); keep the two
in step (test_render_config.py checks this one against the template).

Inputs:
  DEVICES_DIR        one `<name>.pub` per device (ES256 public key, PEM), from
                     scripts/device-key.sh. The file name is the JWT `kid`.
  COUCHDB_UUID       32 hex chars, fixed for the life of the database.
  COUCHDB_DATABASE   default "notes".
  LIVESYNC_PASSWORD  the daemon's CouchDB password.
  E2E_PASSPHRASE     LiveSync's end-to-end passphrase, the same on every device.
  WEB_ALLOWED_EMAIL  optional: the one account oauth2-proxy lets into Obsidian in a browser.
  OUT_DIR            where couchdb/10-agent-notes.ini, livesync/settings.json and web/emails go.
"""

from __future__ import annotations

import json
import os
import re
import string
import sys
from pathlib import Path

KID = re.compile(r"^[a-z0-9-]{1,32}$")
# In the image: next to this file. In the repo: sync/ (one template for every deployment).
_HERE = Path(__file__).resolve().parent
TEMPLATE = next((p for p in (_HERE / "couchdb.ini.tmpl", _HERE.parent / "sync" / "couchdb.ini.tmpl") if p.exists()),
                _HERE / "couchdb.ini.tmpl")


def jwt_keys(devices_dir: Path) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    lines = []
    for f in sorted(devices_dir.glob("*.pub")):
        kid = f.stem
        if not KID.match(kid):
            raise SystemExit(f"{f.name}: device names are lowercase letters, digits and - (max 32)")
        pem = f.read_text().strip()
        key = load_pem_public_key(pem.encode())
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise SystemExit(f"{f.name}: not an ES256 (P-256) public key")
        lines.append(f"ec:{kid} = " + pem.replace("\n", "\\n"))
    return "\n".join(lines)


def couchdb_ini(template: str, uuid: str, database: str, keys: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", uuid):
        raise SystemExit("COUCHDB_UUID: 32 lowercase hex characters (openssl rand -hex 16)")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", database):
        raise SystemExit("COUCHDB_DATABASE: lowercase letters, digits, _ and -, starting with a letter")
    return string.Template(template).substitute(uuid=uuid, database=database, jwt_keys=keys)


def livesync_settings(database: str, password: str, passphrase: str, couch_uri: str) -> str:
    if len(passphrase) < 16:
        raise SystemExit("E2E_PASSPHRASE: at least 16 characters (it encrypts every note in CouchDB)")
    # The settings the daemon needs; the CLI fills in the rest with its
    # defaults. encrypt and usePathObfuscation must match the devices' Setup URI.
    return json.dumps({
        "couchDB_URI": couch_uri,
        "couchDB_USER": "livesync",
        "couchDB_PASSWORD": password,
        "couchDB_DBNAME": database,
        "encrypt": True,
        "passphrase": passphrase,
        "usePathObfuscation": True,
        "liveSync": True,
        "syncOnStart": True,
        "syncOnSave": True,
        "isConfigured": True,
    }, indent=2)


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(path.with_suffix(".tmp"), path)


def main(env: dict[str, str]) -> None:
    out = Path(env.get("OUT_DIR", "/config"))
    database = env.get("COUCHDB_DATABASE", "notes")
    keys = jwt_keys(Path(env.get("DEVICES_DIR", "/devices")))
    if not keys:
        print("render: no device keys yet; Obsidian can't sync until you add one (scripts/device-key.sh)", file=sys.stderr)
    write_private(out / "couchdb" / "10-agent-notes.ini",
                  couchdb_ini(TEMPLATE.read_text(), env["COUCHDB_UUID"], database, keys))
    write_private(out / "livesync" / "settings.json",
                  livesync_settings(database, env["LIVESYNC_PASSWORD"], env["E2E_PASSPHRASE"],
                                    env.get("COUCHDB_INTERNAL_URL", "http://couchdb:5984")))
    email = env.get("WEB_ALLOWED_EMAIL", "").strip()
    if email:
        # oauth2-proxy's authenticated-emails file: the only account let into
        # Obsidian in a browser (no email domains are configured).
        if not re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", email):
            raise SystemExit("WEB_ALLOWED_EMAIL: one email address")
        write_private(out / "web" / "emails", email + "\n")
    print(f"render: couchdb ini and livesync settings written ({keys.count(chr(10)) + 1 if keys else 0} device key(s))")


if __name__ == "__main__":
    main(dict(os.environ))
