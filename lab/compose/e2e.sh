#!/usr/bin/env bash
# End-to-end test of the Docker Compose deployment, on this machine, with
# nothing outside it: a mock OIDC provider, a bare git repo as the remote, TLS
# from Caddy's internal CA, and the LiveSync CLI as a phone set up from a
# Setup URI made by scripts/setup-uri.
#
#   lab/compose/e2e.sh                  (needs docker, node >= 22, openssl, uv)
#   E2E_WEB=1 lab/compose/e2e.sh        also Obsidian in a browser (downloads Obsidian, ~100 MB)
#   E2E_SETUP_ONLY=1 lab/compose/e2e.sh set the lab up and leave it running, untested (to look around or record)
#
# What it proves, each step through the real path:
#   1. a note pushed to the git remote reaches the folder and the phone
#   2. a note the phone writes reaches the folder and the git remote
#   3. a note an MCP tool writes reaches the git remote and the phone
#   4. the gate only lets Bearer requests to the notes database through
#   5. the MCP server talks OIDC to the provider and refuses a call without a token
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/agent-notes-e2e.XXXXXX")
project="agent-notes-e2e-$$"
fails=0

compose() {
  docker compose -p "$project" --project-directory "$repo/deploy/compose" \
    -f "$repo/deploy/compose/compose.yaml" -f "$here/compose.lab.yaml" --env-file "$work/.env" "$@"
}
cleanup() {
  if [[ ${KEEP:-0} == 1 ]]; then echo "KEEP=1: stack $project left running, files in $work"; return; fi
  compose --profile device down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
# eventually <seconds> <description> <command...>: retry until it succeeds.
eventually() {
  local t=$1 what=$2; shift 2
  for _ in $(seq 1 "$t"); do
    if "$@" >/dev/null 2>&1; then ok "$what"; return 0; fi
    sleep 1
  done
  bad "$what"; return 1
}

echo "== setup in $work"
cp "$repo/deploy/compose/.env.example" "$work/.env"
python3 - "$work/.env" <<'EOF'
import sys, re, os
WEB = os.environ.get("E2E_WEB") == "1"
p = sys.argv[1]; s = open(p).read()
for k, v in {
    "COMPOSE_PROFILES": "", "SYNC_HOST": "sync.test", "MCP_HOST": "mcp.test", "WEB_HOST": "web.test",
    "OIDC_ISSUER": "https://idp.test/default", "OIDC_CLIENT_ID": "agent-notes-test", "OIDC_CLIENT_SECRET": "test-secret",
    "AGENT_NOTES_ALLOWED_SUBS": "owner-sub", "GIT_REMOTE": "ssh://git@git-server/srv/notes.git", "GIT_TOKEN_FILE": "",
    "GIT_SSH_KEY": "/secrets/git/id_ed25519", "GIT_SSH_KNOWN_HOSTS": "/secrets/git/known_hosts",
    "JWT_SUB": "owner", "AGENT_NOTES_IMAGE": "agent-notes-server:e2e",
    "DEVICES_DIR": "WORK/devices", "GIT_SECRETS_DIR": "WORK/secrets/git",
    # The mock logs everyone in as intruder@test.example: the default run
    # checks that this account is refused, the web run lets it in.
    "WEB_ALLOWED_EMAIL": "intruder@test.example" if WEB else "owner@test.example",
}.items():
    s = re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M) if re.search(rf"^{k}=", s, re.M) else s + f"{k}={v}\n"
open(p, "w").write(s.replace("WORK/", sys.argv[1].rsplit("/", 1)[0] + "/"))
EOF
"$repo/scripts/init-env.sh" "$work/.env" >/dev/null
mkdir -p "$work/secrets/git"
# The deploy key git-sync pushes with (the git host gets the public half).
ssh-keygen -q -t ed25519 -N "" -C agent-notes-lab -f "$work/secrets/git/id_ed25519"
# git-sync runs as uid 1000 and the runner may not be 1000 (GitHub's is 1001):
# this throwaway key is made readable; git-sync makes its own private copy.
# A real deployment chowns its secrets to 1000 instead (docs/git.md).
chmod 644 "$work/secrets/git/id_ed25519"
AGENT_NOTES_DIR="$work" "$repo/scripts/device-key.sh" phone >/dev/null

echo "== build and start"
docker build -q -f "$repo/server/Dockerfile" -t agent-notes-server:e2e "$repo" >/dev/null
compose run --rm remote-init >/dev/null
compose up -d --wait --wait-timeout 180 couchdb edge idp git-server >/dev/null
# The host key, pinned before git-sync connects: never trusted on first use.
compose exec -T git-server cat /etc/ssh/ssh_host_ed25519_key.pub | awk '{print "git-server", $1, $2}' > "$work/secrets/git/known_hosts"
compose up -d git-sync livesync mcp gate oauth2-proxy >/dev/null
if [[ ${E2E_WEB:-0} == 1 ]]; then compose --profile web up -d --wait --wait-timeout 300 web >/dev/null; fi

git_remote_has() { compose exec -T git-server git --git-dir=/srv/notes.git show "main:$1" | grep -q "$2"; }
folder_has() { compose exec -T mcp sh -c "grep -q '$2' '/vault/notes/$1'"; }
device() { compose --profile device run --rm -T device "$@"; }

echo "== 1. git remote -> folder -> phone"
eventually 90 "the seeded note is in the folder" folder_has agents/digest.md "Be brief"

( cd "$work" && (cd "$repo/scripts/setup-uri" && npm ci --ignore-scripts --no-audit --no-fund -q >/dev/null 2>&1) &&
  SYNC_URL=https://sync.test E2E_PASSPHRASE=$(grep ^E2E_PASSPHRASE= .env | cut -d= -f2-) JWT_SUB=owner \
  SETUP_URI_PASSPHRASE=e2e-uri-pass DEVICE_SECRETS_DIR="$work/secrets/devices" \
  node "$repo/scripts/setup-uri/setup-uri.mjs" phone >"$work/uri.txt" )
uri=$(sed -n 3p "$work/uri.txt")
if echo "e2e-uri-pass" | device setup "$uri" >/dev/null 2>&1; then ok "the phone opened its Setup URI"; else bad "the phone opened its Setup URI"; fi
phone_sees() { device sync >/dev/null 2>&1; device cat "$1" 2>/dev/null | grep -q "$2"; }
eventually 60 "the phone reads the seeded note" phone_sees agents/digest.md "Be brief"
if [[ ${E2E_SETUP_ONLY:-0} == 1 ]]; then
  # A clean, running lab with the phone set up, nothing tested yet.
  echo "SETUP_ONLY project=$project work=$work"; KEEP=1; exit 0
fi

echo "== 2. phone -> folder -> git remote"
printf 'Captured on the phone\n' | device put inbox/from-phone.md >/dev/null 2>&1
device sync >/dev/null 2>&1 || true
eventually 90 "the phone's note is in the folder" folder_has inbox/from-phone.md "Captured on the phone"
eventually 90 "the phone's note is in the git remote" git_remote_has inbox/from-phone.md "Captured on the phone"

echo "== 3. MCP tool -> folder -> git remote and phone"
compose exec -T mcp python - <<'EOF' >/dev/null
import asyncio
from pathlib import Path
from fastmcp import Client
import server
vault = server.Vault(root=Path("/vault/notes"), tmp=Path("/vault/tmp"))
async def main():
    # In-process, no OAuth: the MCP layer and the file path are what's under test here.
    async with Client(server.build_server(vault)) as c:
        await c.call_tool("write_note", {"path": "inbox/from-agent.md", "content": "Written by an agent\n"})
asyncio.run(main())
EOF
eventually 90 "the agent's note is in the git remote" git_remote_has inbox/from-agent.md "Written by an agent"
eventually 90 "the phone reads the agent's note" phone_sees inbox/from-agent.md "Written by an agent"

echo "== 4. the gate"
ca="$work/root.crt"; compose exec -T mcp cat /ca/root.crt >"$ca"
code() { compose exec -T mcp python -c "
import ssl, sys, urllib.request
ctx = ssl.create_default_context(cafile='/ca/root.crt')
req = urllib.request.Request(sys.argv[1], method=sys.argv[2], headers=dict(h.split(': ', 1) for h in sys.argv[3:]))
try: print(urllib.request.urlopen(req, context=ctx, timeout=10).status)
except urllib.error.HTTPError as e: print(e.code)
" "$@"; }
admin_pw=$(grep ^COUCHDB_ADMIN_PASSWORD= "$work/.env" | cut -d= -f2-)
basic=$(printf 'admin:%s' "$admin_pw" | base64)
expect() { local want=$1 what=$2; shift 2; got=$(code "$@"); if [[ $got == "$want" ]]; then ok "$what ($got)"; else bad "$what: got $got, want $want"; fi; }
expect 403 "no auth, root"                  https://sync.test/ GET
expect 403 "admin password, _users"         https://sync.test/_users/_all_docs GET "Authorization: Basic $basic"
expect 403 "admin password, notes database" https://sync.test/notes GET "Authorization: Basic $basic"
expect 403 "a Bearer, _users"               https://sync.test/_users GET "Authorization: Bearer x.y.z"
expect 403 "a Bearer, _security"            https://sync.test/notes/_security GET "Authorization: Bearer x.y.z"
expect 403 "a Bearer, _config"              https://sync.test/_node/_local/_config GET "Authorization: Bearer x.y.z"
# A well-formed device token, the phone's kid, _admin role, signed by a key
# CouchDB doesn't know: past the gate, refused by CouchDB itself (400
# bad_request "Bad signature in authentication token", not 401).
forged=$(compose exec -T mcp python -c "
import time, jwt
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1())
print(jwt.encode({'sub': 'owner', 'exp': int(time.time()) + 300, '_couchdb.roles': ['_admin']}, k, algorithm='ES256', headers={'kid': 'phone'}))")
expect 400 "a forged device token, notes (CouchDB: bad signature)" https://sync.test/notes GET "Authorization: Bearer $forged"
expect 403 "a forged device token, _users"  https://sync.test/_users GET "Authorization: Bearer $forged"

# The phone's real key: the gate must still refuse what a device never needs.
phone=$(cd "$repo/server" && uv run -q python -c "
import sys, time, jwt
print(jwt.encode({'sub': 'owner', 'exp': int(time.time()) + 300, '_couchdb.roles': ['_admin']}, open(sys.argv[1]).read(), algorithm='ES256', headers={'kid': 'phone'}))" "$work/secrets/devices/phone.key")
expect 200 "the phone's key, notes database"  https://sync.test/notes GET "Authorization: Bearer $phone"
expect 403 "an encoded slash: another database" https://sync.test/notes%2Fevil PUT "Authorization: Bearer $phone"
expect 403 "an encoded slash, lowercase"      https://sync.test/notes%2fevil PUT "Authorization: Bearer $phone"
expect 403 "dropping the notes database"      https://sync.test/notes DELETE "Authorization: Bearer $phone"
expect 403 "recreating the notes database"    https://sync.test/notes/ PUT "Authorization: Bearer $phone"
expect 403 "writing a design document"        https://sync.test/notes/_design/x PUT "Authorization: Bearer $phone" "Content-Type: application/json"
expect 403 "COPY"                             https://sync.test/notes/x COPY "Authorization: Bearer $phone"

echo "== 4b. CouchDB itself: a device token is a member of the database, not an admin"
# The same claims as LiveSync's tokens (sub, exp, "_couchdb.roles": ["_admin"],
# hard-coded in the plugin), straight to CouchDB on the internal network, past
# the gate: what the gate can't see (a body) or would miss by mistake.
# couchdb.ini takes the roles from `sub`, so the _admin claim is ignored.
# body <url> <method> <json body> [headers...]: the status, then the response.
body() { compose exec -T mcp python -c "
import ssl, sys, urllib.request
ctx = ssl.create_default_context(cafile='/ca/root.crt')
h = dict(h.split(': ', 1) for h in sys.argv[4:]); h['Content-Type'] = 'application/json'
req = urllib.request.Request(sys.argv[1], method=sys.argv[2], data=sys.argv[3].encode() or None, headers=h)
try: r = urllib.request.urlopen(req, context=ctx, timeout=10); print(r.status, r.read().decode()[:200])
except urllib.error.HTTPError as e: print(e.code, e.read().decode()[:200])
" "$@"; }
refused() { local what=$1; shift; got=$(body "$@"); case ${got%% *} in 401|403) ok "$what (${got%% *})" ;; *) bad "$what: got $got, want 401 or 403" ;; esac; }
couch=http://couchdb:5984
dev="Authorization: Bearer $phone"
adm="Authorization: Basic $basic"
got=$(body $couch/_session GET "" "$dev")
if echo "$got" | grep -q '"roles":\["owner"\]'; then ok "CouchDB sees the device as owner with roles [owner]"; else bad "the device's session: $got"; fi
got=$(body $couch/notes/_local/e2e-probe PUT '{"x":1}' "$dev")
if [[ ${got%% *} == 201 ]]; then ok "the device still writes to the database (a _local document, 201)"; else bad "the device writing a _local document: $got"; fi
refused "direct: _all_dbs"                $couch/_all_dbs GET "" "$dev"
refused "direct: _config"                 $couch/_node/_local/_config GET "" "$dev"
refused "direct: _users"                  $couch/_users/_all_docs GET "" "$dev"
# A member's _security write gets 500 no_majority (each shard refuses it),
# not a 401: check the object, not the status.
body $couch/notes/_security PUT '{"admins":{"names":[],"roles":[]},"members":{"names":[],"roles":[]}}' "$dev" >/dev/null
got=$(body $couch/notes/_security GET "" "$adm")
if echo "$got" | grep -q '"owner"'; then ok "direct: writing notes/_security changes nothing"; else bad "notes/_security after the device wrote it: $got"; fi
refused "direct: create another database" $couch/evil PUT "" "$dev"
refused "direct: PUT a design document"   $couch/notes/_design/evil PUT '{"views":{}}' "$dev"
refused "gate: a design document in a POST /notes body" https://sync.test/notes POST '{"_id":"_design/evil1","validate_doc_update":"function(){throw({forbidden:1})}"}' "$dev"
# _bulk_docs answers 201 with an error per refused document: check after.
got=$(body https://sync.test/notes/_bulk_docs POST '{"docs":[{"_id":"_design/evil2","validate_doc_update":"function(){throw({forbidden:1})}"}]}' "$dev")
echo "       _bulk_docs answered: $got"
got=$(body $couch/notes/_design/evil2 GET "" "$adm")
if [[ ${got%% *} == 404 ]]; then ok "gate: a design document in a _bulk_docs body is not written (404 after)"; else bad "the _bulk_docs design document was written: $got"; fi
# Last: with the old _admin mapping this one really drops the database.
refused "direct: drop the notes database" $couch/notes DELETE "" "$dev"

echo "== 5. MCP and OIDC"
expect 200 "OAuth metadata"                 https://mcp.test/.well-known/oauth-authorization-server GET
expect 401 "an MCP call without a token"    https://mcp.test/mcp POST "Content-Type: application/json"
expect 200 "health"                         https://mcp.test/healthz GET

# A client registered with a loopback redirect only (what makes a client
# trusted) must not finish its login anywhere else.
spoof=$(compose exec -T mcp python - <<'EOF'
import json, ssl, urllib.request, urllib.parse
ctx = ssl.create_default_context(cafile="/ca/root.crt")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect)
def go(url):
    # Where the authorize step sends the browser: the consent page (a login
    # starts) or back to the client with an error (refused).
    try: op.open(urllib.request.Request(url), timeout=10); return "no-redirect"
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        return "consent" if "/consent" in loc else ("error" if "error=" in loc else f"other:{e.code}")
reg = json.loads(op.open(urllib.request.Request("https://mcp.test/register", json.dumps({
    "redirect_uris": ["http://localhost:1/cb"], "client_name": "Claude Code", "token_endpoint_auth_method": "none"}).encode(),
    {"Content-Type": "application/json"}), timeout=10).read())
q = lambda r: urllib.parse.urlencode({"response_type": "code", "client_id": reg["client_id"], "redirect_uri": r,
    "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s", "resource": "https://mcp.test/mcp"})
print(go("https://mcp.test/authorize?" + q("https://claude.ai/api/mcp/auth_callback")),
      go("https://mcp.test/authorize?" + q("http://localhost:4242/cb")))
EOF
)
read -r foreign own <<<"$spoof"
if [[ $foreign == error ]]; then ok "a loopback client can't log in through claude.ai (refused)"; else bad "a loopback client through claude.ai: $foreign, want error"; fi
if [[ $own == consent ]]; then ok "the same client on its own loopback redirect (consent page)"; else bad "the same client on its own loopback redirect: $own"; fi

echo "== 6. Obsidian in a browser: SSO"
web() { compose exec -T mcp python - "$@" <<'EOF'
import ssl, sys, urllib.request, http.cookiejar
ctx = ssl.create_default_context(cafile="/ca/root.crt")
op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def get(url, method="GET"):
    try:
        r = op.open(urllib.request.Request(url, method=method), timeout=60); return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e: return e.code, ""
status, body = get("https://web.test/")          # the whole SSO login, then the app
print(status, "obsidian" in body.lower())
for path, method in sys.argv[1:] and [a.split(":") for a in sys.argv[1:]] or []:
    print(path, get("https://web.test" + path, method)[0])
EOF
}
if [[ ${E2E_WEB:-0} == 1 ]]; then
  out=$(web /api/vault/remove:DELETE /API/Vault/Remove:DELETE //api//vault/create:POST /api/vault/rename:POST)
  first=$(echo "$out" | head -1)
  if [[ $first == "200 True" ]]; then ok "the allowed account logs in through SSO and gets Obsidian"; else bad "the allowed account: $first, want 200 and Obsidian"; fi
  while read -r path code; do
    if [[ $code == 403 ]]; then ok "vault admin refused: $path (403)"; else bad "vault admin $path: $code, want 403"; fi
  done < <(echo "$out" | tail -n +2)
  # A note in a subfolder that arrives while the page is open is read with its
  # path in the query, slashes encoded: the gate must pass that (it refused
  # it once, and such notes failed to open) and still refuse them in the path.
  compose exec -T mcp sh -c 'mkdir -p /vault/notes/inbox/captures && printf "# e2e\n" > /vault/notes/inbox/captures/e2e-web.md'
  out=$(web "/api/fs/readFile?vault=notes&path=inbox%2Fcaptures%2Fe2e-web.md&encoding=utf8:GET" "/api%2Fvault%2Fremove:DELETE")
  nested=$(echo "$out" | sed -n 2p); encoded=$(echo "$out" | sed -n 3p)
  if [[ ${nested##* } == 200 ]]; then ok "a note in a subfolder opens (encoded path in the query: 200)"; else bad "a note in a subfolder: ${nested##* }, want 200"; fi
  if [[ ${encoded##* } == 403 ]]; then ok "an encoded slash in the path is refused (403)"; else bad "an encoded slash in the path: ${encoded##* }, want 403"; fi
  compose exec -T mcp rm -f /vault/notes/inbox/captures/e2e-web.md
else
  first=$(web | head -1)
  if [[ ${first%% *} == 403 ]]; then ok "another account at the provider is refused after login (403)"; else bad "another account at the provider got ${first%% *}, want 403"; fi
fi

echo "== 7. what the git remote may not bring in"
# A push carrying an Obsidian plugin (code the browser app would run) and a
# symlink: git-sync must refuse to merge it, and nothing reaches the folder.
compose exec -T git-server sh -c '
  set -e; git config --global --add safe.directory "*"
  cd /tmp && rm -rf evil && git clone -q /srv/notes.git evil && cd evil
  mkdir -p .obsidian/plugins/x && echo "alert(1)" > .obsidian/plugins/x/main.js && ln -s /etc/passwd inbox-link.md
  git -c user.name=x -c user.email=x@x add -A && git -c user.name=x -c user.email=x@x commit -qm evil && git push -q origin main
  chown -R git:git /srv/notes.git' >/dev/null 2>&1
refused() { compose logs git-sync 2>&1 | grep -q "status=refused reason=unsafe_remote"; }
eventually 60 "git-sync refused a remote carrying a plugin and a symlink" refused
if compose exec -T mcp sh -c 'test ! -e /vault/notes/.obsidian/plugins/x/main.js && test ! -e /vault/notes/inbox-link.md'; then ok "neither reached the folder"; else bad "the plugin or the symlink reached the folder"; fi

echo "== 8. an MCP client logs in, is restricted, gets elevated with a fresh second factor; another session reviews its inbox items"
flow=$(compose exec -T mcp python - < "$here/oauth_flow.py" 2>&1) || true
echo "$flow" | sed 's/^/  /' | grep -E "^  (ok|FAIL) " || echo "$flow" | tail -5 | sed 's/^/  /'
n=$(echo "$flow" | grep -c "^FAIL " || true); [[ $(echo "$flow" | grep -c "^ok ") -ge 22 ]] || n=$((n + 1))
fails=$((fails + n))

echo
if [[ $fails -gt 0 ]]; then
  echo "e2e: $fails check(s) failed"
  for svc in git-sync git-server livesync mcp couchdb gate; do echo "--- $svc"; compose logs --no-log-prefix --tail 15 "$svc" 2>&1; done
  echo "(KEEP=1 to keep the stack)"
  exit 1
fi
echo "e2e: all checks passed"
