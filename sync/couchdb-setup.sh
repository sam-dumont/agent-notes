#!/bin/sh
# One-shot CouchDB setup for agent-notes. Idempotent: safe to rerun, and rerun
# whenever this script or a password changes.
#   - the notes database (COUCHDB_DATABASE, default "notes")
#   - user `livesync` (the sync daemon), password LIVESYNC_PASSWORD
#   - <db>/_security: members are livesync and the devices' JWT subject.
#     Not admins: the daemon can read and write documents, nothing else.
set -eu
: "${COUCH:?}" "${COUCHDB_USER:?}" "${COUCHDB_PASSWORD:?}" "${LIVESYNC_PASSWORD:?}" "${JWT_SUB:?}"
DB="${COUCHDB_DATABASE:-notes}"

# The password goes in a JSON body. It is base64url (scripts/init-env.sh), so
# no quoting issue; refuse anything else rather than build broken JSON.
case "$LIVESYNC_PASSWORD" in *[!A-Za-z0-9_-]*) echo "LIVESYNC_PASSWORD: use base64url characters only (openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"; exit 1 ;; esac
case "$DB$JWT_SUB" in *[!a-z0-9_-]*) echo "COUCHDB_DATABASE and JWT_SUB: lowercase letters, digits, _ and - only"; exit 1 ;; esac

# The admin credentials go to curl in a file, never on its command line.
umask 077
netrc=$(mktemp)
trap 'rm -f "$netrc"' EXIT
host=$(echo "$COUCH" | sed -E 's#^https?://([^:/]+).*#\1#')
printf 'machine %s login %s password %s\n' "$host" "$COUCHDB_USER" "$COUCHDB_PASSWORD" > "$netrc"
c() { curl -sS --netrc-file "$netrc" -H 'Content-Type: application/json' "$@"; }

tries=0
until curl -sf "$COUCH/_up" >/dev/null; do
  tries=$((tries + 1)); [ "$tries" -gt 60 ] && { echo "CouchDB never came up"; exit 1; }
  echo "waiting for CouchDB"; sleep 5
done

code=$(c -o /dev/null -w '%{http_code}' -X PUT "$COUCH/$DB")
case "$code" in 201|202|412) echo "database $DB: $code" ;; *) echo "database $DB: HTTP $code"; exit 1 ;; esac

rev=$(c "$COUCH/_users/org.couchdb.user:livesync" | sed -n 's/.*"_rev":"\([^"]*\)".*/\1/p')
body=$(printf '{"name":"livesync","type":"user","roles":[],"password":"%s"%s}' "$LIVESYNC_PASSWORD" "${rev:+,\"_rev\":\"$rev\"}")
printf '%s' "$body" | c -f -o /dev/null -X PUT "$COUCH/_users/org.couchdb.user:livesync" --data-binary @-
echo "user livesync: ok"

printf '{"admins":{"names":[],"roles":[]},"members":{"names":["livesync","%s"],"roles":[]}}' "$JWT_SUB" \
  | c -f -o /dev/null -X PUT "$COUCH/$DB/_security" --data-binary @-
echo "$DB/_security: ok"
