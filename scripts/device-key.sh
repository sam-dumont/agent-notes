#!/usr/bin/env bash
# New LiveSync device: an ES256 key pair, one per device.
#
#   scripts/device-key.sh <name>        e.g. phone, laptop, work-tablet
#
# Writes the public key to devices/<name>.pub (deployed: CouchDB trusts it)
# and the private key to secrets/devices/<name>.key, mode 600 (never deployed:
# it goes to the device inside its Setup URI, scripts/setup-uri). Nothing is
# printed. Then redeploy so CouchDB loads the key, and build the device's
# Setup URI. Revoke a device: delete devices/<name>.pub and redeploy; its
# tokens stop working when CouchDB restarts.
set -euo pipefail

name=${1:?usage: $0 <device-name>}
[[ $name =~ ^[a-z0-9-]{1,32}$ ]] || { echo "name: lowercase letters, digits and - only (max 32)" >&2; exit 1; }

root=${AGENT_NOTES_DIR:-.}
pub="$root/devices/$name.pub"
key="$root/secrets/devices/$name.key"
[[ -e $pub || -e $key ]] && { echo "$name already exists ($pub or $key)" >&2; exit 1; }

umask 077
mkdir -p "$root/devices" "$root/secrets/devices"
openssl ecparam -name prime256v1 -genkey -noout 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -out "$key"
chmod 600 "$key"
( umask 022; openssl ec -in "$key" -pubout -out "$pub" 2>/dev/null )

echo "device $name: public key $pub, private key $key (mode 600)."
echo "Next: redeploy (CouchDB reads devices/), then: node scripts/setup-uri/setup-uri.mjs $name"
