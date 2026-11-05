# Devices

Two different things are called devices here.

## Obsidian devices (LiveSync)

Each phone, tablet or laptop running Obsidian gets its own ES256 key pair.
CouchDB trusts the public key; the device signs short JWTs with the private
key.

### Add one

```bash
scripts/device-key.sh phone                     # devices/phone.pub + secrets/devices/phone.key (600)
(cd deploy/compose && docker compose up -d --force-recreate render couchdb)   # CouchDB reloads the keys
cd scripts/setup-uri && npm ci && cd -
SYNC_URL=https://sync.example.com E2E_PASSPHRASE=... node scripts/setup-uri/setup-uri.mjs phone
```

(`E2E_PASSPHRASE` is the one in your `.env`. Export it from there rather than
typing it, so it stays out of your shell history.)

The script prints a Setup URI and a passphrase. It builds the URI from the
LiveSync library's own defaults, the same ones the server's daemon uses, so the
device and the server agree on document ids and path handling. By default the
plugin only opens the URI until the end of the current 7-day window
(`--persistent` drops that). That is a convenience, not a control: the window
is a public number and the check is the reader's clock, so a stolen URI and
passphrase still decrypt later. Revoking the device is the only control.

On the device: install **Self-hosted LiveSync**, then *Use a Setup URI*, paste,
enter the passphrase. When it asks, **fetch from the server**. Never overwrite
the server from a new device: that replaces every note with this device's
(empty) vault.

Send the URI and the passphrase over two different channels. Together they are
the device's full credentials (its key, the sync address and the end-to-end
passphrase). Then delete `secrets/devices/phone.key` if you don't need to
rebuild the URI.

### Revoke one

Delete `devices/phone.pub`, then `docker compose up -d --force-recreate render
couchdb` (or apply Terraform). CouchDB restarts without the key and the
device's tokens stop working.

### Conflicts

Two devices editing the same note offline make a LiveSync conflict. Obsidian
asks which version to keep. Until you choose, CouchDB keeps both and the daemon
doesn't write that note to the folder, so git keeps the last good copy.

## Enrolled machines (MCP)

Optional, and the way to trust a machine: a client registered with loopback
redirects isn't trusted by default (`AGENT_NOTES_TRUST_LOOPBACK`), because a
loopback redirect can't prove which program is listening. A machine whose MCP
sessions should be trusted without asking for elevation (your own laptop)
proves itself with a device token:
`hooks/mcp-device-header` signs a 12-hour ES256 token with a key kept on that
machine (a macOS Keychain item, or a mode-600 file) and Claude Code sends it as
`X-MCP-Device` next to the normal OAuth login. The server checks it against
`AGENT_NOTES_DEVICE_KEYS` (`{"laptop": "<public key PEM>"}`). The token's
audience is the server's `AGENT_NOTES_NAME`, so a token for one server is
useless on another.

The OIDC login and the `sub` allowlist still apply to every request. But every
session on an enrolled machine is trusted, with no approval step: treat that
key like a root SSH key. Neither store is hardware-backed: any process running
as your user can read the Keychain item (its access list trusts
`/usr/bin/security`) or the file, and a token is good for 12 hours wherever it
is replayed. Enroll only a machine where you'd accept that. Revoke it by removing it from
`AGENT_NOTES_DEVICE_KEYS` and restarting the server. See `hooks/README.md`.
