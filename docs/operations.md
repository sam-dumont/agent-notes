# Operations

## What to alert on

Every component logs one line per outcome; match on the fields, not the text.

| Line | Means | Do |
|---|---|---|
| `agent-notes-git: status=refused reason=mass_delete` | more than 20 notes vanished in one round; nothing committed since | below |
| `agent-notes-git: status=refused reason=unsafe_remote` | the remote carries a hidden path, symlink or submodule; nothing merged since | find who pushed it; revert it on the remote |
| `agent-notes-git: status=conflict` | a device and an agent edited the same lines; the markers are in the note everywhere | edit the note on any device |
| `agent-notes-git: status=error` (repeated) | fetch or push failing | the token or key (`gh-app-token` lines on GitHub App) |
| MCP `event=auth result=denied` | another account at your provider reached the server | check who; rotate if it's a stolen session |
| MCP `event=call result=refused`, several in a few minutes from one client | a session trying to write where it can't | read what that session was doing |
| MCP `event=elevation result=approved` | you (or someone with your second factor) approved a grant | expected, once per approval |
| MCP `event=elevation result=refused` | a live grant id used from another client or another conversation's login: it leaked (a transcript, a note, a log) | find where the id went; to end the grant before its 4 hours, restart the server (grants live in memory) |
| MCP `event=auth result=client_registered` | a new MCP client registered | expected when you add a connector |

With `OTEL_EXPORTER_OTLP_ENDPOINT` set, the server also sends traces and the
counters `agent_notes.calls`, `agent_notes.elevations`, `agent_notes.auth` and
the gauge `agent_notes.grants`.

## A refused mass deletion

```bash
docker compose exec git-sync git status --short | head -50
# a mistake (wiped folder, LiveSync reset): bring the notes back
docker compose exec git-sync git checkout -- .
# a real cleanup: commit it once, by hand
docker compose exec git-sync git commit -qam "notes: remove <what>"
```

Restored files flow back to CouchDB and the devices on their own.

## Backups

git is the backup of the notes: every change is a commit on the remote. Each
volume rebuilds from the other:

- **Notes volume lost.** Start again: git-sync adopts the remote's history and
  writes the files out, the daemon pulls from CouchDB; files on disk win over
  the repo's copy.
- **CouchDB lost.** The folder is complete. Recreate the database (the setup
  step does it), then from one device: LiveSync *Rebuild everything >
  Overwrite server data with this device's files*. Every other device fetches.

Back up `.env` (or your Terraform secrets) somewhere safe: without
`E2E_PASSPHRASE` no device can read CouchDB.

## Upgrades

- **LiveSync**: bump the CLI image with the Obsidian plugin version your
  devices run. Same sync core, so keep them close.
- **Ignis and Obsidian**: bump together. Each Ignis release targets one Obsidian
  release (its tag says which); `OBSIDIAN_VERSION` and `OBSIDIAN_SHA256` follow.
- **The server**: Docker Compose builds it from your checkout by default. The
  published snapshot is `ghcr.io/sam-dumont/agent-notes-server:0.1.0`: pin it by
  the digest shown on its package page (github.com/sam-dumont/agent-notes/pkgs/container/agent-notes-server)
  (`AGENT_NOTES_IMAGE` in `.env`, `images.server` in Terraform). Check its
  provenance first: `gh attestation verify oci://ghcr.io/sam-dumont/agent-notes-server@sha256:<digest> -R sam-dumont/agent-notes`.
  The Terraform module refuses a registry image without a digest.

## Rotation

| Secret | How | Effect |
|---|---|---|
| a device key | revoke, then add the device again | that device only |
| `AGENT_NOTES_JWT_SIGNING_KEY` | new value, restart `mcp` | every MCP client logs in again |
| `LIVESYNC_PASSWORD` | new value, rerun `couchdb-setup`, restart `livesync` | none |
| `COUCHDB_ADMIN_PASSWORD` | new value, recreate `couchdb` | none |
| `E2E_PASSPHRASE` | don't, unless it leaked: every device needs a new Setup URI and a full rebuild | everything |
| `COUCHDB_UUID` | never | replication checkpoints restart from zero |
