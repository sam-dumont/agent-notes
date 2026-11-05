# Architecture

One folder of Markdown, four ways in.

```
Obsidian (laptop, phone) --TLS--> edge --> gate :5985 --> CouchDB <--> livesync daemon --+
agents with a checkout   --git--> your git remote <---------------------- git-sync ------+-- the notes folder
claude.ai, ChatGPT,      --TLS--> edge --> MCP server (OAuth via your OIDC provider) ---+
Claude Code                                                                             |
any browser              --TLS--> edge --> oauth2-proxy --> gate :8081 --> Ignis -------+
```

| Piece | What it does | Image |
|---|---|---|
| CouchDB | The sync database for Obsidian's Self-hosted LiveSync plugin. Holds ciphertext only (end-to-end encryption and path obfuscation are on). | `couchdb` |
| livesync | The LiveSync CLI as a daemon: the same sync core as the plugin, keeping a plain folder of `.md` files in step with CouchDB. | `vrtmrz/livesync-cli` |
| git-sync | Commits the folder to your git remote every 30 s and merges what agents pushed (`sync/git-sync.sh`). The git dir lives outside the folder, so `.git` never reaches the devices. | `alpine/git` |
| MCP server | The notes as MCP tools, with access tiers and owner-approved elevation (`server/`). | this repo |
| gate | The only way in from outside to CouchDB and to Ignis (`sync/gate.Caddyfile`). | `caddy` |
| Ignis | Obsidian's own app served as a web page, for machines where you can't install it. Optional. | `nobbe/ignis` |
| oauth2-proxy | SSO in front of Ignis, which has no login of its own. Optional. | `oauth2-proxy` |

An edit anywhere reaches everywhere else in under a minute: a device writes to
CouchDB, the daemon writes the file, git-sync commits it; an agent's push is
merged by git-sync, the daemon sees the file and writes it to CouchDB, the
devices pull it.

## Why each piece is there

**Why CouchDB and not git on the devices.** Obsidian on a phone can't run git
in the background. LiveSync syncs live, offline-first, encrypted, and is
already what a lot of Obsidian users run.

**Why a daemon and not the MCP server writing to CouchDB.** The folder is the
contract: the daemon, git-sync, the MCP server and Ignis all read and write
plain files. Each can be replaced without touching the others.

**Why git at all.** History (every change is a commit), a backup off the
machine, and the easiest way in for cloud agents, which clone repos anyway.

**Why the gate.** LiveSync's JWTs carry the `_admin` role, hard-coded in the
plugin. CouchDB takes the roles from `sub` instead, so a device token is a
member of the notes database (no design documents, no `_config`), and the
gate adds what membership doesn't cover: only Bearer requests to the notes
database get through, no dropping it, no `COPY` (docs/security.md).

## Files on the notes volume

| Path | Owner | What |
|---|---|---|
| `notes/` | everyone (uid 1000) | the notes |
| `git/` | git-sync | the git dir (`GIT_DIR`), outside the notes |
| `livesync/` | livesync | the daemon's local database |
| `tmp/` | MCP server | atomic writes land here first, then a rename into `notes/` |
| `state/` | MCP server | OAuth client registrations and upstream tokens, Fernet-encrypted |
| `ignis/` | Ignis | its own settings |
