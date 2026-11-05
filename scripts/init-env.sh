#!/usr/bin/env bash
# Fill the empty secret lines of a .env file with random values. Never prints
# a secret, never overwrites one already set (rotating E2E_PASSPHRASE or
# COUCHDB_UUID on a live setup breaks every device: docs/operations.md).
#
#   scripts/init-env.sh deploy/compose/.env
set -euo pipefail
f=${1:?usage: $0 <path to .env>}
[[ -f $f ]] || { echo "$f not found: copy .env.example first" >&2; exit 1; }
chmod 600 "$f"

b64url() { openssl rand -base64 "$1" | tr '+/' '-_' | tr -d '=\n'; }
gen() {
  case $1 in
    COUCHDB_UUID) openssl rand -hex 16 ;;
    AGENT_NOTES_STORAGE_KEY) openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n' ;;  # Fernet: urlsafe base64 of 32 bytes, padded
    OAUTH2_PROXY_COOKIE_SECRET) b64url 24 | cut -c1-32 ;;                            # 32 bytes for AES-256
    E2E_PASSPHRASE) b64url 30 ;;
    *) b64url 32 ;;
  esac
}

tmp=$(mktemp "$f.XXXXXX")
trap 'rm -f "$tmp"' EXIT
filled=()
while IFS= read -r line || [[ -n $line ]]; do
  key=${line%%=*}
  case $key in
    COUCHDB_UUID|COUCHDB_ADMIN_PASSWORD|COUCHDB_COOKIE_SECRET|LIVESYNC_PASSWORD|E2E_PASSPHRASE|AGENT_NOTES_JWT_SIGNING_KEY|AGENT_NOTES_STORAGE_KEY|OAUTH2_PROXY_COOKIE_SECRET)
      if [[ $line == "$key=" ]]; then
        printf '%s=%s\n' "$key" "$(gen "$key")" >>"$tmp"
        filled+=("$key")
        continue
      fi ;;
  esac
  printf '%s\n' "$line" >>"$tmp"
done <"$f"
chmod 600 "$tmp"
mv "$tmp" "$f"
trap - EXIT
echo "filled: ${filled[*]:-nothing (all set already)}"
