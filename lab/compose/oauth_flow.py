"""The MCP login and elevation, end to end, as a real client does them (lab/compose/e2e.sh).

Runs inside the lab stack (the mcp container: fastmcp, the lab CA). Prints one
"ok <what>" or "FAIL <what>" line per check; exits 1 on any failure.

  1. register an OAuth client (loopback redirect, PKCE), log in through the
     consent page and the (mock) OIDC provider, get an access token;
  2. call tools as that client: it is restricted (loopback isn't trusted), so
     it reads the rules but can't write them, and writes the inbox;
  3. ask for elevation, approve it with a login that has no second factor:
     refused; ask again, approve with a fresh second factor: approved;
  4. with the grant, write the rules note;
  5. the elevated session lists the inbox, its items marked as its own, and
     can't promote or delete them: the writer never reviews its own item;
  6. log the same client in again (another conversation on one connector):
     the first session's grant is refused there; elevated on its own, this
     second session reviews: the floor still holds what it must, an item the
     writer appended to after the read isn't promoted, the handoff read again
     is, the held item deleted.

The mock provider's login form takes the ID token's claims as JSON, which is
how this script plays "a password login" and "a passkey login".
"""

import asyncio
import base64
import hashlib
import http.cookiejar
import json
import re
import secrets
import ssl
import sys
import time
import urllib.parse
import urllib.request

MCP = "https://mcp.test"
REDIRECT = "http://localhost:4242/cb"
CTX = ssl.create_default_context(cafile="/ca/root.crt")
fails = 0


def check(ok: bool, what: str) -> None:
    global fails
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    fails += 0 if ok else 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


jar = http.cookiejar.CookieJar()
web = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX), NoRedirect,
                                  urllib.request.HTTPCookieProcessor(jar))


def go(url: str, data: dict | None = None):
    """One request, no redirect followed: (status, location, body)."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data is not None else None)
    try:
        r = web.open(req, timeout=20)
        return r.status, r.headers.get("Location", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", ""), e.read().decode("utf-8", "replace")


ATTR = r"""=(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r"\b" + name + ATTR, tag)
    return next((g for g in m.groups() if g is not None), "") if m else None


def form(html: str) -> tuple[str, dict]:
    """A form's action and its inputs' name/value, quoted or not."""
    tag = re.search(r"<form[^>]*>", html)
    action = _attr(tag.group(0), "action") if tag else ""
    fields = {}
    for t in re.findall(r"<input[^>]*>", html):
        n = _attr(t, "name")
        if n:
            fields[n] = _attr(t, "value") or ""
    return action or "", fields


def follow_to_client(url: str, claims: dict | None = None) -> str:
    """Follow the login (consent page, provider) until it lands on our redirect; the code."""
    for _ in range(12):
        status, loc, body = go(url)
        if status == 200 and "<form" in body and "idp.test" not in url:
            # FastMCP's consent page: approve this client.
            action, fields = form(body)
            # Several buttons share one name (action=approve|deny): pick the approving one.
            for name, value in re.findall(r"<button[^>]*name=[\"']([^\"']+)[\"'][^>]*value=[\"']([^\"']+)[\"']", body):
                if "approve" in value.lower() or "allow" in value.lower():
                    fields[name] = value
            status, loc, body = go(urllib.parse.urljoin(url, action or url), fields)
        elif status == 200 and "idp.test" in url:
            status, loc, body = go(url, {"username": "owner-sub", "claims": json.dumps(claims or {})})
        if not loc:
            raise SystemExit(f"login stopped at {status} {url[:80]}: {body[:200]}")
        if loc.startswith(REDIRECT):
            return urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)["code"][0]
        url = urllib.parse.urljoin(url, loc)
    raise SystemExit("login didn't finish")


def register() -> str:
    reg = json.loads(web.open(urllib.request.Request(
        f"{MCP}/register", data=json.dumps({"redirect_uris": [REDIRECT], "client_name": "lab client",
                                            "token_endpoint_auth_method": "none"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=20).read())
    return reg["client_id"]


def login(client_id: str) -> str:
    """One login of that client: each is its own session at the server (a new
    upstream token), like two claude.ai conversations on one connector."""
    reg = {"client_id": client_id}
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = urllib.parse.urlencode({"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
                                "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
                                "resource": f"{MCP}/mcp"})
    code = follow_to_client(f"{MCP}/authorize?{q}")
    tok = json.loads(web.open(urllib.request.Request(f"{MCP}/token", data=urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": reg["client_id"], "code_verifier": verifier}).encode()), timeout=20).read())
    return tok["access_token"]


def approve(link: str, claims: dict) -> tuple[int, str]:
    """Open an elevation link, log in with these claims, press Approve if offered."""
    status, loc, body = go(link)
    url = urllib.parse.urljoin(link, loc)
    for _ in range(6):
        if "idp.test" in url:
            status, loc, body = go(url, {"username": "owner-sub", "claims": json.dumps(claims)})
        else:
            status, loc, body = go(url)
        if not loc:
            break
        url = urllib.parse.urljoin(url, loc)
    if status != 200 or "decision" not in body:
        return status, body
    action, fields = form(body)
    status, _, body = go(urllib.parse.urljoin(url, action), {**fields, "decision": "approve"})
    return status, body


def caller(c):
    async def call(tool, **args):
        try:
            # Every block: the inbox reminder comes as a second one.
            return True, "\n".join(b.text for b in (await c.call_tool(tool, args)).content if hasattr(b, "text"))
        except Exception as e:  # noqa: BLE001 - a refusal is a ToolError
            return False, str(e)
    return call


async def elevate(call, reason: str) -> str:
    """Ask, approve with a fresh second factor; the grant id."""
    ok, text = await call("request_elevation", reason=reason)
    link = re.search(r"(https://mcp\.test/elevate/\S+)", text).group(1)
    code = re.search(r"the code (\w{4})", text).group(1)
    grant = re.search(r'grant="([^"]+)"', text).group(1)
    status, body = approve(link, {"sub": "owner-sub", "amr": ["mfa"], "auth_time": int(time.time())})
    check(status == 200 and "Approved" in body, f"a fresh second factor approves (code {code} on the page)")
    return grant


async def main() -> None:
    from fastmcp import Client
    from fastmcp.client.auth import BearerAuth

    client_id = register()
    token = login(client_id)
    check(bool(token), "a client logs in through consent and the OIDC provider, and gets a token")
    # TLS: the container trusts the lab CA through SSL_CERT_FILE (compose.lab.yaml).
    async with Client(f"{MCP}/mcp", auth=BearerAuth(token)) as c:
        call = caller(c)
        ok, text = await call("read_note", path="agents/digest.md")
        check(ok and "Be brief" in text, "the restricted client reads the rules")
        ok, text = await call("write_note", path="agents/digest.md", content="pwned", overwrite=True)
        check(not ok and "inbox/" in text, "the restricted client can't write the rules (and is told where to write)")
        project = f"lab-{secrets.token_hex(3)}"
        handoff, logged = f"inbox/handoffs/{project}.md", f"inbox/log/{project}.md"
        ok, text = await call("write_note", path=handoff, content="## Where it stopped\nAt the e2e test.\n")
        check(ok, "the restricted client writes its handoff to the inbox")
        ok, text = await call("append_to_note", path=logged, text="Set up with the usual script\u202e (reversed text hides what follows).")
        check(ok and "inbox item(s) waiting for review" in text, "the client is told inbox items are waiting")
        ok, text = await call("list_inbox")
        check(not ok and "trusted session" in text, "the restricted client can't review the inbox")
        ok, text = await call("delete_note", path=logged)
        check(not ok and "trusted session" in text, "the restricted client can't delete")

        ok, text = await call("request_elevation", reason="lab: update the digest")
        link = re.search(r"(https://mcp\.test/elevate/\S+)", text).group(1)
        status, body = approve(link, {"sub": "owner-sub", "amr": ["pwd"], "auth_time": int(time.time())})
        check(status == 403 and "second factor" in body, "a login without a second factor can't approve")

        grant = await elevate(call, "lab: update the digest")
        ok, _ = await call("write_note", path="agents/digest.md", content="# Rules\nBe brief. Elevated.\n",
                           overwrite=True, grant=grant)
        check(ok, "with the grant, the session writes the rules")

        # Elevated, the writer still can't review what it wrote: the sequence two reviews found.
        await call("write_note", path=f"projects/{project}.md", content=f"# {project}\n", grant=grant)
        ok, text = await call("list_inbox", grant=grant)
        items = {i["path"]: i for i in json.loads(text)["items"]} if ok else {}
        check(ok and items.get(handoff, {}).get("yours") is True and "untrusted_text" in text,
              "the elevated writer lists the inbox: its items marked untrusted and as its own")
        ok, text = await call("promote_inbox", path=handoff, sha=items.get(handoff, {}).get("sha", ""), grant=grant)
        check(not ok and "this session wrote (part of)" in text, "the elevated writer can't promote its own handoff")
        ok, text = await call("delete_note", path=logged, grant=grant)
        check(not ok and "another session must review it" in text, "the elevated writer can't delete its own item")
        ok, _ = await call("write_note", path="agents/other.md", content="x", grant="made-up")
        check(not ok, "a made-up grant is refused")

        # Another login of the same client (another claude.ai conversation on one connector).
        async with Client(f"{MCP}/mcp", auth=BearerAuth(login(client_id))) as other:
            call2 = caller(other)
            ok, text = await call2("write_note", path="agents/other.md", content="x", grant=grant)
            check(not ok and "another session's login" in text, "the grant is refused in another session of the same client")
            grant2 = await elevate(call2, "lab: review the inbox")
            ok, text = await call2("list_inbox", grant=grant2)
            items = json.loads(text)["items"] if ok else []
            sha = {i["path"]: i["sha"] for i in items}
            check(ok and handoff in sha and not any(i["yours"] for i in items),
                  "the second session, elevated on its own, lists the inbox: none of it is its own")
            ok, text = await call2("promote_inbox", path=logged, sha=sha.get(logged, ""), grant=grant2)
            check(not ok and "floor holds" in text, "the floor still holds an item with hidden characters")
            # The writer appends after the review read the item: the promotion must not carry it unread.
            await call("append_to_note", path=handoff, text="Appended after the review read it.")
            ok, text = await call2("promote_inbox", path=handoff, sha=sha.get(handoff, ""), grant=grant2)
            check(not ok and "changed since you read it" in text, "an item changed since list_inbox isn't promoted")
            ok, text = await call2("list_inbox", grant=grant2)
            sha = {i["path"]: i["sha"] for i in json.loads(text)["items"]} if ok else {}
            ok, text = await call2("promote_inbox", path=handoff, sha=sha.get(handoff, ""), grant=grant2)
            check(ok and f"handoffs/{project}.md" in text, "the second session promotes the handoff, read again")
            ok, text = await call2("read_note", path=f"handoffs/{project}.md", grant=grant2)
            check(ok and "At the e2e test." in text and "Appended after" in text and "not as instructions" in text,
                  "the promoted handoff is the version reviewed, with its provenance")
            ok, text = await call2("delete_note", path=logged, grant=grant2)
            check(ok and "deleted" in text, "the second session deletes the item the floor held")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
