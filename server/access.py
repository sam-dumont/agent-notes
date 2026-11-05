"""Who may do what in the vault: access tiers, elevation grants, the approval page.

Every caller is the owner (one OIDC `sub`, AGENT_NOTES_ALLOWED_SUBS), so identity
can't tell a vetted session from one that reads a public repo's issues and might
carry a prompt injection. What differs is the way in:

- **trusted**: one of the owner's enrolled devices (a device token, see
  Devices), any session holding an approved elevation grant, or, only when
  AGENT_NOTES_TRUST_LOOPBACK is on, a client registered with loopback
  redirects only. Off by default: the MCP spec warns that a loopback redirect
  can't prove which local process is listening, and the login is the same
  owner either way. Everything the server offers.
- **restricted**: everything else, e.g. claude.ai (web, phone, cloud sessions)
  and ChatGPT. Reads all but the private paths; writes only the quick-capture
  and handoff folders. The rules folder stays out of reach: a note there is
  loaded by future sessions, so one poisoned write would outlive the session
  that made it.

Elevation is per session without any session id (the MCP protocol of
2026-07-28 has none): `request_elevation` returns a grant id and an approval
link; the owner opens the link, logs in afresh with a second factor, approves;
the session then passes the grant id to the tools. The grant is bound to what
asked for it: the OAuth client id and the session's own login (one
authorization at the OAuth proxy, stable across token refreshes). Every
claude.ai conversation shares one client id, so the client alone isn't
enough: a grant id that leaks into another conversation is refused there,
and the attempt is logged. Grants live in memory (one replica): a restart
drops them, which is fine.

No device binding: a call carrying a valid device token is trusted before any
grant is looked at, so a device never asks for one.

The policy (prefix lists) comes from the deployment's environment, never from
the vault: a session can't widen its own rights by writing a note.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import secrets
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import jwt
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

import telemetry

log = logging.getLogger("agent-notes")

TRUSTED = "trusted"
RESTRICTED = "restricted"

PENDING_TTL = 10 * 60  # a request nobody approves dies quickly
GRANT_TTL = 4 * 60 * 60  # an approved session: an afternoon of work
LOGIN_TTL = 10 * 60  # the provider round trip (state, then the form)
MAX_PENDING = 3  # per client: an injected session can't flood the owner with links
MAX_PENDING_TOTAL = 20  # every client together, a bound on memory
MAX_REASON = 300
MAX_LOGINS = 20  # approval logins in flight: a leaked ticket can't fill memory
# The approval needs a second factor used just now, not a remembered session.
MAX_AUTH_AGE = 5 * 60
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def norm(path: str) -> str:
    """One spelling per path for prefix checks: NFC (a filesystem that
    normalises names, like APFS, must not let an NFD spelling slip past a
    private prefix) and casefolded (so must a case-insensitive one)."""
    return unicodedata.normalize("NFC", path).casefold()


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(norm(p.strip()) for p in value.split(",") if p.strip())


@dataclass(frozen=True)
class Policy:
    """What a restricted caller may touch. Paths are vault-relative, lowercase
    prefixes: `inbox/` is a folder, `consulting/x.md` a single note."""

    # Only the inbox: handoffs, logs and design notes are loaded into later
    # sessions, often trusted local ones with a shell, so a restricted session
    # leaves them in inbox/<kind>/ and the inbox review promotes them
    # (reviewer/, docs/inbox.md).
    write_prefixes: tuple[str, ...] = ("inbox/",)
    append_prefixes: tuple[str, ...] = ("inbox/",)
    private_prefixes: tuple[str, ...] = ()
    trusted_clients: frozenset[str] = frozenset()
    # Trust a client because it registered loopback redirects only. Off by
    # default (see the module docstring); device tokens are the way to trust
    # a machine.
    trust_loopback: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Policy:
        base = cls()
        return cls(
            write_prefixes=_csv(env.get("AGENT_NOTES_WRITE_PREFIXES"), base.write_prefixes),
            append_prefixes=_csv(env.get("AGENT_NOTES_APPEND_PREFIXES"), base.append_prefixes),
            private_prefixes=_csv(env.get("AGENT_NOTES_PRIVATE_PREFIXES"), base.private_prefixes),
            trusted_clients=frozenset(_csv(env.get("AGENT_NOTES_TRUSTED_CLIENTS"), ())),
            trust_loopback=env.get("AGENT_NOTES_TRUST_LOOPBACK", "").strip().lower() in ("1", "true", "yes", "on"),
        )

    def trusts(self, caller: "Caller", auth_configured: bool) -> bool:
        """Trusted by who the client is: a client id the deployment lists, a
        loopback client when trust_loopback is on, or anything in-process
        (no OAuth at all: tests and local runs)."""
        if caller.client in self.trusted_clients:
            return True
        return caller.loopback and (self.trust_loopback or not auth_configured)

    @staticmethod
    def _under(path: str, prefixes: tuple[str, ...]) -> bool:
        p = norm(path)
        return any(p.startswith(norm(x)) for x in prefixes)

    def private(self, path: str) -> bool:
        return self._under(path, self.private_prefixes)

    # Restricted sessions never write these, wherever the prefixes allow: the
    # review's own record (its reasons reach the trusted reviewer) and the
    # inbox's instructions; and no file named like what agents load as
    # instructions (a session working in a clone of the notes would).
    RESERVED = ("inbox/_held.md", "inbox/README.md")
    INSTRUCTION_FILES = {"claude.md", "claude.local.md", "agents.md", "gemini.md", ".cursorrules",
                         ".windsurfrules", "copilot-instructions.md"}

    def _allowed(self, path: str, prefixes: tuple[str, ...]) -> bool:
        name = unicodedata.normalize("NFKC", path.rsplit("/", 1)[-1]).casefold()
        return (self._under(path, prefixes) and not self.private(path)
                and norm(path) not in {norm(r) for r in self.RESERVED} and name not in self.INSTRUCTION_FILES)

    def may_write(self, path: str) -> bool:
        return self._allowed(path, self.write_prefixes)

    def may_append(self, path: str) -> bool:
        return self._allowed(path, self.append_prefixes)


@dataclass
class Grant:
    # Two handles: `id` is the capability the session passes to the tools,
    # `ticket` goes in the approval link. Links end up in reverse-proxy
    # access logs; a ticket there only starts an approval, which still needs
    # the owner's fresh login.
    id: str
    ticket: str
    client: str
    reason: str
    created: float
    expires: float
    status: str = "pending"  # pending | approved | denied
    # Shown in the session and on the approval page, so the owner can tell
    # which conversation asked: every claude.ai conversation is one client.
    code: str = ""
    # The session's own login (Caller.session): a grant works for the
    # authorization that asked, not for every conversation on the client id.
    session: str = ""

    @property
    def ref(self) -> str:
        # What the logs carry: the id is a capability, never logged whole.
        return hashlib.sha256(self.id.encode()).hexdigest()[:10]


class Grants:
    """In-memory grants, plus the two short-lived handles of the approval
    flow (OAuth state, then a one-time form token). All guarded by one lock:
    tools run in worker threads and routes on the event loop."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._grants: dict[str, Grant] = {}
        self._states: dict[str, tuple[str, str, float]] = {}  # state -> grant, nonce, created
        self._forms: dict[str, tuple[str, str, float]] = {}  # token -> grant, sub, created

    def _purge(self, now: float) -> None:
        for g in self._grants.values():
            if g.expires <= now and g.status in ("pending", "approved"):
                # pending: nobody answered the link; approved: the 4 h ran out.
                elevation_event("expired" if g.status == "pending" else "ended", g)
        self._grants = {k: g for k, g in self._grants.items() if g.expires > now}
        self._states = {k: v for k, v in self._states.items() if v[2] + LOGIN_TTL > now}
        self._forms = {k: v for k, v in self._forms.items() if v[2] + LOGIN_TTL > now}

    def request(self, client: str, reason: str, session: str = "") -> Grant:
        now = self.clock()
        with self._lock:
            self._purge(now)
            pending = [g for g in self._grants.values() if g.status == "pending"]
            if sum(g.client == client for g in pending) >= MAX_PENDING or len(pending) >= MAX_PENDING_TOTAL:
                raise PermissionError("too many elevation requests waiting; approve or let them expire (10 min)")
            g = Grant(
                id=secrets.token_urlsafe(24), ticket=secrets.token_urlsafe(18),
                client=client, reason=reason, created=now, expires=now + PENDING_TTL,
                code="".join(secrets.choice("ACDEFHJKMNPRTUVWXY347") for _ in range(4)),
                session=session,
            )
            self._grants[g.id] = g
            return g

    def get(self, grant_id: str) -> Grant | None:
        with self._lock:
            self._purge(self.clock())
            return self._grants.get(grant_id)

    def counts(self) -> dict[str, int]:
        """Grants in memory by status, for the agent_notes.grants gauge."""
        with self._lock:
            self._purge(self.clock())
            out = {"pending": 0, "approved": 0, "denied": 0}
            for g in self._grants.values():
                out[g.status] = out.get(g.status, 0) + 1
            return out

    def by_ticket(self, ticket: str) -> Grant | None:
        with self._lock:
            self._purge(self.clock())
            return next((g for g in self._grants.values() if secrets.compare_digest(g.ticket, ticket)), None)

    def refusal(self, grant_id: str, client: str, session: str) -> str | None:
        """Why the grant doesn't elevate this call, or None when it does:
        approved, unexpired, and used by the client and the session's login
        that asked."""
        g = self.get(grant_id) if grant_id else None
        if g is None or g.status != "approved":
            return "grant not valid here (unknown, expired, or not approved yet)"
        if not secrets.compare_digest(g.client, client):
            return "grant not valid here: it belongs to another client"
        if not g.session or not secrets.compare_digest(g.session, session or ""):
            # Same client id is not the same session: every claude.ai
            # conversation shares one, and so does every Claude Code install
            # behind a URL client id.
            return "grant not valid here: it belongs to another session's login"
        return None

    def active(self, grant_id: str, client: str, session: str = "") -> Grant | None:
        """The grant, if it elevates this call (refusal() says why not)."""
        if self.refusal(grant_id, client, session) is not None:
            return None
        return self.get(grant_id)

    def decide(self, grant_id: str, approve: bool) -> Grant | None:
        now = self.clock()
        with self._lock:
            self._purge(now)
            g = self._grants.get(grant_id)
            if g is None or g.status != "pending":
                return None
            g.status = "approved" if approve else "denied"
            # A denied grant lingers until its pending deadline so a retry of
            # the same link says "denied", then disappears.
            if approve:
                g.expires = now + GRANT_TTL
            return g

    def new_state(self, grant_id: str) -> tuple[str, str] | None:
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        with self._lock:
            self._purge(self.clock())
            if len(self._states) >= MAX_LOGINS:
                return None
            self._states[state] = (grant_id, nonce, self.clock())
        return state, nonce

    def take_state(self, state: str) -> tuple[str, str] | None:
        with self._lock:
            self._purge(self.clock())
            v = self._states.pop(state, None)
        return (v[0], v[1]) if v else None

    def new_form(self, grant_id: str, sub: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._forms[token] = (grant_id, sub, self.clock())
        return token

    def take_form(self, token: str) -> tuple[str, str] | None:
        with self._lock:
            self._purge(self.clock())
            v = self._forms.pop(token, None)
        return (v[0], v[1]) if v else None


def elevation_event(event: str, grant: Grant, level: int = logging.INFO, **fields: object) -> None:
    """requested, approved, denied, expired, ended, refused (a live grant
    used by another client or session), login_failed, login_refused: the counter, and one audit line with the grant's ref
    (never its id or ticket)."""
    telemetry.TELEMETRY.elevations.add(1, {"event": event})
    telemetry.emit("elevation", level, result=event, grant=grant.ref, client=grant.client, **fields)


# --- Devices ----------------------------------------------------------------
#
# The owner's own machines, trusted without an elevation. Optional: off unless
# AGENT_NOTES_DEVICE_KEYS lists public keys. A client id can't prove a machine
# (a URL-based client id is a public document anyone can use from anywhere),
# so the machine proves itself: a helper on it (hooks/mcp-device-header for
# Claude Code's headersHelper) signs a short-lived ES256 token with a key that
# never leaves the machine and sends it as X-MCP-Device, next to the normal
# OAuth login. The public keys come from the deployment (kid -> PEM), never
# from the vault. A device token alone opens nothing: the OIDC login and the
# sub allowlist still apply to every request. Treat an enrolled machine like
# a root SSH key: whoever controls it gets the trusted tier.

DEVICE_HEADER = "x-mcp-device"
DEVICE_MAX_LIFETIME = 24 * 3600  # one working day; the helper mints a new one per connection


class DeviceKeys:
    def __init__(self, keys: dict[str, str] | None = None, audience: str = "agent-notes",
                 clock: Callable[[], float] = time.time):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        self.keys = {}
        for kid, pem in (keys or {}).items():
            key = load_pem_public_key(pem.encode())
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
                raise ValueError(f"device key {kid!r}: not a P-256 (ES256) public key")
            self.keys[kid] = key
        self.audience = audience
        self.clock = clock

    @classmethod
    def from_env(cls, env: dict[str, str]) -> DeviceKeys:
        raw = env.get("AGENT_NOTES_DEVICE_KEYS", "").strip()
        return cls(json.loads(raw) if raw else {}, audience=env.get("AGENT_NOTES_NAME", "agent-notes"))

    def verify(self, token: str) -> str | None:
        """The device id (kid) when the token is a valid, fresh device token; None otherwise."""
        if not token or not self.keys or len(token) > 4096:
            return None
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
            key = self.keys.get(kid)
            if key is None:
                return None
            claims = jwt.decode(token, key, algorithms=["ES256"], audience=self.audience,
                                options={"require": ["exp", "iat", "aud"]}, leeway=60)
            now = self.clock()
            if claims["exp"] - claims["iat"] > DEVICE_MAX_LIFETIME or claims["iat"] > now + 60:
                return None
            return kid
        except Exception:  # noqa: BLE001 - any doubt is a restricted caller
            return None


def device_from_request(devices: DeviceKeys) -> str | None:
    if not devices.keys:
        return None
    return devices.verify(get_http_headers(include={DEVICE_HEADER}).get(DEVICE_HEADER, ""))


def redirect_registered(client_id: str, redirect_uri: str, registered: list[str], upstream_client_id: str) -> bool:
    """Whether an authorization request may use this redirect URI.

    FastMCP checks a dynamically registered client's requested redirect only
    against the proxy-wide allow list (claude.ai, ChatGPT, loopback), not
    against the client's own registration. identify() trusts a client because
    it registered loopback redirects only, so without this a client registered
    with http://localhost could finish its login on claude.ai's callback and
    be trusted from anywhere. Loopback ports may vary (RFC 8252); anything
    else must match a registered URI exactly. URL client ids (CIMD) are
    checked by FastMCP against their own document, and the upstream id is
    never trusted, so both pass here.
    """
    if client_id == upstream_client_id or client_id.startswith(("http://", "https://")):
        return True
    want = urlsplit(redirect_uri)
    for r in registered:
        reg = urlsplit(r)
        if r == redirect_uri:
            return True
        if (reg.scheme == want.scheme == "http" and (reg.hostname or "") in LOOPBACK_HOSTS
                and reg.hostname == want.hostname and reg.path == want.path and reg.query == want.query):
            return True
    return False


@dataclass(frozen=True)
class Caller:
    client: str  # the MCP client id, '-' when unknown
    loopback: bool  # registered with loopback redirects only (a local client)
    # The session's own login: a hash of the OAuth proxy's upstream token id,
    # which one authorization-code exchange creates and every refresh keeps
    # (fastmcp oauth_proxy: JTIMapping.upstream_token_id). '' when unknown,
    # and then nothing can be bound to it (request_elevation refuses).
    session: str = ""


def session_ref(upstream_token_id: str) -> str:
    """What stands for a login in grants and logs: the raw id is a key to the
    stored upstream tokens, so it never leaves this process."""
    return hashlib.sha256(("agent-notes-session:" + upstream_token_id).encode()).hexdigest()[:24]


async def _session(auth: Any, claims: dict) -> str:
    """The login behind a verified proxy JWT. FastMCP keeps a jti -> upstream
    token id mapping for each access token it issues; a refresh issues a new
    jti mapped to the same id, so the id lasts as long as the connector's
    login. Private API of FastMCP's proxy (4.1.6, pinned; a test runs the real
    one): anything unexpected is '' (fails closed: no elevation for that call)."""
    jti = claims.get("jti")
    store = getattr(auth, "_jti_mapping_store", None)
    if not jti or store is None:
        return ""
    mapping = await store.get(key=str(jti))
    upstream = getattr(mapping, "upstream_token_id", "") if mapping is not None else ""
    return session_ref(str(upstream)) if upstream else ""


async def identify(auth: Any, upstream_client_id: str) -> Caller:
    """The MCP client and the session's login behind the current request.

    FastMCP hands tools the *upstream* (OIDC provider) token, whose client is
    always our one OIDC app; the MCP client is only in the proxy's own JWT, so read
    and verify that from the Authorization header, then look up how the client
    registered. Fails closed: anything unexpected is an unknown, non-loopback
    client with no session.
    """
    if auth is None:
        return Caller(client="local", loopback=True, session="local")  # no OAuth at all: tests, local runs
    bearer = get_http_headers(include={"authorization"}).get("authorization", "")
    if not bearer.lower().startswith("bearer "):
        return Caller(client="-", loopback=False)
    client, session = "-", ""
    try:
        claims = auth.jwt_issuer.verify_token(bearer[7:].strip())
        client = str(claims.get("client_id", "")) or "-"
        session = await _session(auth, claims)
        # Two kinds of client are never local whatever they claim:
        # - the upstream id: claude.ai skips registration and FastMCP then
        #   synthesizes a client whose redirect is http://localhost;
        # - URL ids (CIMD): the redirects come from a document anyone can host.
        if client == upstream_client_id or client.startswith(("http://", "https://")):
            return Caller(client=client, loopback=False, session=session)
        info = await auth.get_client(client)
    except Exception:  # noqa: BLE001 - fail closed, log the type only
        telemetry.emit("client_lookup", logging.WARNING, result="failed", client=client)
        return Caller(client=client, loopback=False, session=session)
    uris = [urlsplit(str(u)) for u in (getattr(info, "redirect_uris", None) or [])]
    loopback = bool(uris) and all(u.scheme == "http" and (u.hostname or "") in LOOPBACK_HOSTS for u in uris)
    return Caller(client=client, loopback=loopback, session=session)


# --- Approval page --------------------------------------------------------

@dataclass(frozen=True)
class StepUp:
    """What proves a fresh second factor in the approval login's ID token.

    Providers say it differently, so it's configuration, not code:
    - `amr` (RFC 8176): "mfa" from most providers; "phr"/"phrh" for passkeys
      where the provider reports them (AGENT_NOTES_STRONG_AMR).
    - `acr`: Keycloak, Authentik and others name a step-up level instead;
      list the values that mean "second factor" (AGENT_NOTES_STRONG_ACR), and
      ask for one at login with AGENT_NOTES_ACR_VALUES.
    - a custom claim listing the login's methods with timestamps
      (AGENT_NOTES_METHODS_CLAIM): Auth0 puts nothing in `amr` after a passkey
      login, so a post-login Action has to copy the methods into the token
      (docs/oidc.md). A method counts only if it is in STRONG_METHODS and was
      used within MAX_AUTH_AGE.
    Nothing configured that the token carries means every approval is refused:
    the check fails closed.
    """

    strong_amr: frozenset[str] = frozenset({"mfa", "phr", "phrh"})
    strong_acr: frozenset[str] = frozenset()
    methods_claim: str = ""
    strong_methods: frozenset[str] = frozenset({"passkey", "mfa"})
    acr_values: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str]) -> StepUp:
        base = cls()
        return cls(
            strong_amr=frozenset(_csv(env.get("AGENT_NOTES_STRONG_AMR"), tuple(base.strong_amr))),
            strong_acr=frozenset(_csv(env.get("AGENT_NOTES_STRONG_ACR"), ())),
            methods_claim=env.get("AGENT_NOTES_METHODS_CLAIM", "").strip(),
            strong_methods=frozenset(_csv(env.get("AGENT_NOTES_STRONG_METHODS"), tuple(base.strong_methods))),
            acr_values=env.get("AGENT_NOTES_ACR_VALUES", "").strip(),
        )


class Discovery:
    """The provider's OpenID configuration, fetched once and cached."""

    def __init__(self, issuer: str, fetch: Callable[[str], dict] | None = None):
        self.issuer = issuer.rstrip("/")
        self._fetch = fetch or _get_json
        self._doc: dict | None = None
        self._lock = threading.Lock()

    @property
    def doc(self) -> dict:
        with self._lock:
            if self._doc is None:
                doc = self._fetch(f"{self.issuer}/.well-known/openid-configuration")
                # The document must describe the issuer we were given, or a
                # spoofed discovery could point the login anywhere.
                if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
                    raise ValueError("discovery document is for another issuer")
                for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not str(doc.get(k, "")).startswith("https://") and not _loopback_url(str(doc.get(k, ""))):
                        raise ValueError(f"discovery: {k} must be https")
                self._doc = doc
            return self._doc


def _loopback_url(url: str) -> bool:
    u = urlsplit(url)
    return u.scheme == "http" and (u.hostname or "") in LOOPBACK_HOSTS


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - https enforced by the caller
        return json.load(r)


@dataclass(frozen=True)
class Approver:
    """The OIDC login behind the approval page: the same OIDC client as the
    MCP proxy (its redirect URIs gain /elevate/callback), with a forced fresh
    login (`prompt=login`, `max_age=0`). The page checks `auth_time` and the
    StepUp proof itself (check_login), so a provider that skips the second
    factor fails closed instead of open."""

    discovery: Discovery
    client_id: str
    client_secret: str
    base_url: str
    allowed_subs: frozenset[str]
    step_up: StepUp = StepUp()
    # Injectable for tests: (code) -> verified ID token claims.
    exchange: Callable[[str], dict] | None = None

    @property
    def callback(self) -> str:
        return f"{self.base_url.rstrip('/')}/elevate/callback"

    def link(self, grant: Grant) -> str:
        return f"{self.base_url.rstrip('/')}/elevate/{grant.ticket}"

    def authorize_url(self, state: str, nonce: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.callback,
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "max_age": "0",
            "prompt": "login",
        }
        if self.step_up.acr_values:
            params["acr_values"] = self.step_up.acr_values
        endpoint = self.discovery.doc["authorization_endpoint"]
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{urllib.parse.urlencode(params)}"

    def _exchange(self, code: str) -> dict:
        doc = self.discovery.doc
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.callback,
        }).encode()
        req = urllib.request.Request(
            doc["token_endpoint"], data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - https checked in Discovery
            id_token = json.load(r)["id_token"]
        algs = [a for a in doc.get("id_token_signing_alg_values_supported", ["RS256"]) if a != "none"]
        key = jwt.PyJWKClient(doc["jwks_uri"]).get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token, key.key, algorithms=algs, audience=self.client_id,
            issuer=doc["issuer"], options={"require": ["exp", "iat", "sub", "nonce"]},
        )

    async def claims(self, code: str) -> dict:
        return await asyncio.to_thread(self.exchange or self._exchange, code)


def _epoch(value: object) -> float | None:
    """A method timestamp: ISO 8601 (what Auth0 Actions give) or epoch s/ms."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _amr(claims: dict) -> list:
    amr = claims.get("amr")
    return amr if isinstance(amr, list) else ([amr] if isinstance(amr, str) else [])


def strong_factor(claims: dict, now: float, step_up: StepUp = StepUp()) -> bool:
    """A strong factor in amr, a strong acr, or a strong method in the
    methods claim used within MAX_AUTH_AGE. auth_time is checked separately,
    so an amr from a stale login never gets this far."""
    if step_up.strong_amr & {str(a) for a in _amr(claims)}:
        return True
    if step_up.strong_acr and str(claims.get("acr", "")) in step_up.strong_acr:
        return True
    if not step_up.methods_claim:
        return False
    methods = claims.get(step_up.methods_claim)
    if not isinstance(methods, list):
        return False
    for m in methods:
        if isinstance(m, dict) and m.get("name") in step_up.strong_methods:
            ts = _epoch(m.get("timestamp"))
            # A method from an older session doesn't count: this login was
            # forced fresh (max_age=0), so its factor is minutes old at most.
            if ts is not None and 0 <= now - ts <= MAX_AUTH_AGE:
                return True
    return False


def login_diagnostics(claims: dict, now: float, step_up: StepUp = StepUp()) -> str:
    """What the provider sent about how this login was made: amr, acr, the
    method names from the methods claim and their age in seconds. Shown on
    the refusal page only after the sub check passed (so only to the owner),
    and logged: no token, no timestamps, no ids."""
    parts = [f"amr: {_amr(claims) or 'absent'}", f"acr: {claims.get('acr') or 'absent'}"]
    if step_up.methods_claim:
        methods = []
        for m in claims.get(step_up.methods_claim) or []:
            if isinstance(m, dict):
                ts = _epoch(m.get("timestamp"))
                age = f"{int(now - ts)} s ago" if ts is not None else "unreadable time"
                methods.append(f"{m.get('name')} ({age})")
        claim = "absent" if step_up.methods_claim not in claims else (", ".join(methods) or "empty")
        parts.append(f"methods claim: {claim}")
    auth_time = claims.get("auth_time")
    parts.append(f"login: {int(now - auth_time)} s ago" if isinstance(auth_time, (int, float)) else "login: absent")
    return "; ".join(parts)


def check_login(claims: dict, nonce: str, allowed_subs: frozenset[str], now: float,
                step_up: StepUp = StepUp()) -> str | None:
    """Why an approval login is refused, or None when it's good."""
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        return "nonce mismatch"
    if str(claims.get("sub", "")) not in allowed_subs:
        return "not the owner"
    auth_time = claims.get("auth_time")
    if not isinstance(auth_time, (int, float)) or now - auth_time > MAX_AUTH_AGE:
        return "login not fresh"
    if not strong_factor(claims, now, step_up):
        return (
            "no fresh second factor in this login: the provider must report one in amr or acr, or in the "
            "methods claim (AGENT_NOTES_STRONG_AMR, _STRONG_ACR, _METHODS_CLAIM; docs/oidc.md)"
        )
    return None


_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:2rem auto;padding:0 1rem}"
        "blockquote{border-left:3px solid #888;margin:1rem 0;padding:.25rem 1rem;white-space:pre-wrap;word-break:break-word}"
        "button{font-size:1rem;padding:.6rem 1.2rem;margin-right:.5rem}</style>"
        f"<h1>{html.escape(title)}</h1>{body}"
    )
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def clean_reason(reason: str) -> str:
    # Model-written text: one line, printable, short. Escaped again on output.
    text = " ".join("".join(c if c.isprintable() else " " for c in reason).split())
    return text[:MAX_REASON]


def register_routes(mcp: Any, grants: Grants, approver: Approver) -> None:
    @mcp.custom_route("/elevate/callback", methods=["GET"])
    async def callback(request: Request):
        q = request.query_params
        taken = grants.take_state(q.get("state", ""))
        if taken is None or "code" not in q:
            return page("Link expired", "<p>Ask the session for a new elevation link.</p>", 400)
        grant_id, nonce = taken
        grant = grants.get(grant_id)
        if grant is None or grant.status != "pending":
            return page("Nothing to approve", "<p>This request expired or was already decided.</p>", 410)
        try:
            claims = await approver.claims(q["code"])
        except Exception as e:  # noqa: BLE001
            elevation_event("login_failed", grant, logging.WARNING, error=type(e).__name__)
            return page("Login failed", "<p>The provider did not return a valid login. Try the link again.</p>", 400)
        refused = check_login(claims, nonce, approver.allowed_subs, grants.clock(), approver.step_up)
        if refused:
            # How the login was made, for the log and, once the sub is known
            # to be the owner's, for the page: the next refusal explains
            # itself instead of needing a log query.
            owner = str(claims.get("sub", "")) in approver.allowed_subs
            detail = login_diagnostics(claims, grants.clock(), approver.step_up)
            elevation_event(
                "login_refused", grant, logging.WARNING,
                why=refused, sub=str(claims.get("sub", "")), detail=detail,
            )
            extra = f"<p><small>What the provider sent: {html.escape(detail)}</small></p>" if owner else ""
            return page("Refused", f"<p>{html.escape(refused)}</p>{extra}", 403)
        form = grants.new_form(grant.id, str(claims["sub"]))
        minutes = max(0, int((grant.expires - grants.clock()) // 60))
        body = (
            f"<p>A session asks for full access to the vault (every note, every folder) for {GRANT_TTL // 3600} hours.</p>"
            f"<p>Its code: <strong style='font-size:1.5rem;letter-spacing:.2rem'>{html.escape(grant.code)}</strong>"
            " (the session showed you the same one)</p>"
            f"<p>Its reason:</p><blockquote>{html.escape(grant.reason) or '<em>none given</em>'}</blockquote>"
            f"<p>Client <code>{html.escape(grant.client)}</code>, asked {int((grants.clock() - grant.created) // 60)} min ago,"
            f" expires in {minutes} min if nobody answers.</p>"
            "<p><strong>Approve only if you asked this session to elevate just now and the code matches.</strong>"
            " A request you didn't expect may come from text the session read (an issue, a PR, a web page).</p>"
            f"<form method=post action='/elevate/{html.escape(grant.ticket)}/decision'>"
            f"<input type=hidden name=token value='{html.escape(form)}'>"
            f"<button name=decision value=approve>Approve for {GRANT_TTL // 3600} h</button>"
            "<button name=decision value=deny>Deny</button></form>"
        )
        return page("Elevate a session?", body)

    @mcp.custom_route("/elevate/{ticket}/decision", methods=["POST"])
    async def decision(request: Request):
        raw = (await request.body())[:4096].decode("utf-8", "replace")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        grant = grants.by_ticket(request.path_params["ticket"])
        taken = grants.take_form(form.get("token", ""))
        if grant is None or taken is None or not secrets.compare_digest(taken[0], grant.id):
            return page("Link expired", "<p>Open the elevation link again.</p>", 400)
        approve = form.get("decision") == "approve"
        g = grants.decide(grant.id, approve)
        if g is None:
            return page("Nothing to approve", "<p>This request expired or was already decided.</p>", 410)
        elevation_event(g.status, g, sub=taken[1], until=int(g.expires) if approve else None)
        if approve:
            until = time.strftime("%H:%M UTC", time.gmtime(g.expires))
            return page("Approved", f"<p>The session has full access until {until}. Go back to it: it can carry on.</p>")
        return page("Denied", "<p>The session stays restricted.</p>")

    @mcp.custom_route("/elevate/{ticket}", methods=["GET"])
    async def start(request: Request):
        grant = grants.by_ticket(request.path_params["ticket"])
        if grant is None or grant.status != "pending":
            return page("Nothing to approve", "<p>This request expired or was already decided.</p>", 410)
        login = grants.new_state(grant.id)
        if login is None:
            return page("Busy", "<p>Too many approval logins in flight. Wait 10 minutes.</p>", 429)
        state, nonce = login
        return RedirectResponse(approver.authorize_url(state, nonce), status_code=303, headers=_HEADERS)
