#!/usr/bin/env bash
# The Terraform module on a throwaway kind cluster: apply, then check that the
# pods run under PSA restricted, the setup Job completes, a note goes from the
# git remote to the folder and from an MCP tool back to the remote, CouchDB
# gets the daemon's documents, and the gate refuses everything but device
# tokens on the notes database. Deletes the cluster at the end (KEEP=1 keeps it).
#
#   lab/kind/run.sh                    (needs kind, docker, terraform, kubectl, openssl)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
cluster=agent-notes-test
work=$(mktemp -d "${TMPDIR:-/tmp}/agent-notes-kind.XXXXXX")
kc="$work/kubeconfig"
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
k() { kubectl --kubeconfig "$kc" "$@"; }
cleanup() {
  [[ -n ${pf:-} ]] && kill "$pf" 2>/dev/null || true
  if [[ ${KEEP:-0} == 1 ]]; then echo "KEEP=1: cluster $cluster kept (kubeconfig $kc)"; return; fi
  kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
  rm -rf "$work" "$here/.terraform" "$here/.terraform.lock.hcl" "$here/terraform.tfstate" "$here/terraform.tfstate.backup"
}
trap cleanup EXIT
eventually() {
  local t=$1 what=$2; shift 2
  for _ in $(seq 1 "$t"); do if "$@" >/dev/null 2>&1; then ok "$what"; return 0; fi; sleep 2; done
  bad "$what"; return 1
}

echo "== cluster and image"
kind create cluster --name "$cluster" --kubeconfig "$kc" --wait 120s >/dev/null 2>&1
docker build -q -f "$repo/server/Dockerfile" -t agent-notes-server:kind "$repo" >/dev/null
kind load docker-image --name "$cluster" agent-notes-server:kind >/dev/null

echo "== apply"
mkdir -p "$work/devices"
AGENT_NOTES_DIR="$work" "$repo/scripts/device-key.sh" phone >/dev/null
devices=$(python3 -c 'import json,sys; print(json.dumps({"phone": open(sys.argv[1]).read()}))' "$work/devices/phone.pub")
cd "$here"
terraform init -input=false >/dev/null
terraform apply -input=false -auto-approve -var "kubeconfig=$kc" -var "server_image=agent-notes-server:kind" \
  -var "devices=$devices" >"$work/apply.log" 2>&1 || { tail -30 "$work/apply.log"; exit 1; }
ok "terraform apply ($(grep -o '[0-9]* added' "$work/apply.log" | tail -1))"

ns=notes-test
echo "== workloads"
if [[ $(k get ns $ns -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}') == restricted ]]; then ok "namespace enforces PSA restricted"; else bad "namespace enforces PSA restricted"; fi
eventually 90 "the setup Job completed" sh -c "kubectl --kubeconfig '$kc' -n $ns get jobs -o jsonpath='{.items[*].status.succeeded}' | grep -q 1"
eventually 120 "every pod Ready" sh -c "kubectl --kubeconfig '$kc' -n $ns wait --for=condition=Ready pod --all --field-selector=status.phase!=Succeeded --timeout=5s"

notes_pod() { k -n $ns get pod -l app.kubernetes.io/name=notes -o jsonpath='{.items[0].metadata.name}'; }
pod=$(notes_pod)
inpod() { k -n $ns exec "$pod" -c "$1" -- sh -c "$2"; }

echo "== sync"
eventually 60 "the seeded note is in the folder (git remote -> folder)" inpod mcp "grep -q 'Be brief' /vault/notes/agents/digest.md"
inpod mcp "python - <<'PY'
import asyncio
from pathlib import Path
from fastmcp import Client
import server
v = server.Vault(root=Path('/vault/notes'), tmp=Path('/vault/tmp'))
async def main():
    async with Client(server.build_server(v)) as c:
        await c.call_tool('write_note', {'path': 'inbox/from-agent.md', 'content': 'Written by an agent'})
asyncio.run(main())
PY" >/dev/null
remote_has() { k -n fixtures exec deploy/git -- git --git-dir=/srv/notes.git show main:inbox/from-agent.md | grep -q "Written by an agent"; }
eventually 60 "an MCP write reached the git remote" remote_has

echo "== the gate (port-forward to the sync Service)"
k -n $ns port-forward svc/sync 15985:5985 >/dev/null 2>&1 & pf=$!
sleep 3
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
expect() { local want=$1 what=$2; shift 2; local got; got=$(code "$@"); if [[ $got == "$want" ]]; then ok "$what ($got)"; else bad "$what: got $got, want $want"; fi; }
forged=$(cd "$repo/server" && uv run -q python -c "
import time, jwt
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1())
print(jwt.encode({'sub': 'owner', 'exp': int(time.time()) + 300, '_couchdb.roles': ['_admin']}, k, algorithm='ES256', headers={'kid': 'phone'}))")
expect 403 "no auth"                  http://127.0.0.1:15985/
expect 403 "basic auth, _users"       -u admin:x http://127.0.0.1:15985/_users
expect 403 "a Bearer, _users"         -H "Authorization: Bearer $forged" http://127.0.0.1:15985/_users
expect 403 "a Bearer, _security"      -H "Authorization: Bearer $forged" http://127.0.0.1:15985/notes/_security
expect 400 "a forged token, notes (CouchDB: bad signature)" -H "Authorization: Bearer $forged" http://127.0.0.1:15985/notes

# The phone's real key, signed here: proves CouchDB loaded the device keys.
real=$(cd "$repo/server" && uv run -q python -c "
import sys, time, jwt
key = open(sys.argv[1]).read()
print(jwt.encode({'sub': 'owner', 'exp': int(time.time()) + 300, '_couchdb.roles': ['_admin']}, key, algorithm='ES256', headers={'kid': 'phone'}))" "$work/secrets/devices/phone.key")
docs() { curl -s -H "Authorization: Bearer $real" http://127.0.0.1:15985/notes | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("doc_count",0) > 0 else 1)'; }
eventually 30 "the phone's key reads the notes database, and the daemon filled it" docs

echo "== MCP"
k -n $ns port-forward svc/mcp 18000:8000 >/dev/null 2>&1 & pf2=$!
sleep 3
expect 200 "health"                    http://127.0.0.1:18000/healthz
expect 401 "an MCP call without a token" -X POST -H 'Content-Type: application/json' http://127.0.0.1:18000/mcp
kill $pf2 2>/dev/null || true

echo
if [[ $fails -gt 0 ]]; then echo "kind: $fails check(s) failed"; k -n $ns get pods >&2; exit 1; fi
echo "kind: all checks passed"
