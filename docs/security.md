# Security model

The notes end up in every future agent session: the rules and the handoffs are
loaded at the start of each one. That makes them the most valuable thing to
tamper with. A session working on a public repo reads text anyone can write
(issues, pull requests, web pages). If some of it says "update the house rules"
and the session can write what the next session loads, a one-off prompt
injection becomes permanent. So remote sessions can't write anything that is
loaded into another session, and what is loaded (handoffs) is marked as data.

## Who can reach what

| Way in | Proves who | Can reach |
|---|---|---|
| Obsidian (LiveSync) | an ES256 JWT, one key per device (`scripts/device-key.sh`) | the notes database only, through the gate |
| MCP | an OIDC login at your provider, then `sub` in `AGENT_NOTES_ALLOWED_SUBS` | the tools, in a tier (below) |
| git | whatever your git host checks | the notes repo |
| Ignis | oauth2-proxy (OIDC) and one allowed email (`WEB_ALLOWED_EMAIL`, no email domains) | the notes folder only: like a trusted session, no tiers |

## MCP tiers

Every MCP caller is you, so the login can't tell a careful session from one
that just read a poisoned issue. The way in can:

| Tier | Who | Can |
|---|---|---|
| restricted | remote clients: claude.ai (web, phone, cloud sessions), ChatGPT, anything the server doesn't recognise | read everything but `AGENT_NOTES_PRIVATE_PREFIXES`; write and append only under `inbox/`, in typed folders that the inbox review promotes ([docs/inbox.md](inbox.md)) |
| trusted | an enrolled machine (a device token, docs/devices.md) or a session you approved; optionally (`AGENT_NOTES_TRUST_LOOPBACK`, off by default) a client registered with loopback redirects only | list, read, search, write, append, move, delete, review the inbox |

- Only trusted sessions delete (`delete_note`). Git keeps every deleted note's
  history, and git-sync refuses a commit that deletes more than 20 notes at
  once. A restricted session can't: it could erase the evidence of what it read
  (a held item, another session's handoff). What a session loads is the current
  notes, never the history, so deleting a malicious note takes it out of every
  session; the copy in git stays as a record of where it came from.
- The policy lives in the deployment's environment, never in the notes, so a
  session can't widen its own rights by writing a note.
- A client that identifies itself with a URL (a client metadata document) is
  never trusted on that alone: anyone can host or reuse that document.
- **Loopback isn't proof.** A client registered with loopback redirects only
  is probably a local app (Claude Code), but the MCP spec warns that a loopback
  redirect can't prove which local program is listening, and anyone can
  register such a client. So it isn't trusted unless you turn on
  `AGENT_NOTES_TRUST_LOOPBACK`. To trust your own machine, enroll it: a device
  token signed by a key on that machine (docs/devices.md).
- A client's login can only finish on a redirect URI it registered (loopback
  ports may vary). That is the MCP spec's exact-match requirement, not
  something new: FastMCP's proxy checks requested redirects against its global
  allow list only (the same gap as its issue #3037, fixed in February 2026 and
  back in 4.1.6), so the server enforces it itself. `lab/compose/e2e.sh`
  replays the attack: a client registered with a localhost redirect trying to
  finish its login on claude.ai.
- Restricted sessions write only under `inbox/`: handoffs, logs and design
  notes are loaded into later sessions, often trusted local ones with a shell.
  They leave them in `inbox/handoffs/`, `inbox/log/`, `inbox/decisions/`, and
  the inbox review promotes them: a model judges what each item says, code
  holds invisible characters and instruction-file names, and restricted
  sessions can only create there, never overwrite, move or touch the review's
  own record ([docs/inbox.md](inbox.md)).
- **Who reviews an item, in code.** A restricted session can't review the
  inbox at all. A trusted one can't review an item it wrote, even once
  elevated: every inbox write (`write_note`, `append_to_note`, a move into the
  inbox) records the caller's session ref (a hash of its connector login) as
  a writer of that item, and `promote_inbox`, `hold_inbox` and `delete_note`
  refuse a caller in that list, audited as `result=refused why=writer`. An
  append by a second session makes both writers. A caller with no session ref
  can't prove it isn't the writer, so it's refused on any item with writers.
  The record lives in the server's state directory (`inbox-writers.json` in
  `AGENT_NOTES_STATE_DIR`), not in the notes, which git and the devices sync
  and a session could edit. Entries go when the item is promoted, deleted or
  moved out; an unreadable record makes every current item unreviewable over
  MCP (the scheduled review and you still can). Promotion writes only the
  version that was judged: `promote_inbox` takes the `sha` from `list_inbox`
  and refuses an item that changed since.
- Write rights are checked on the resolved path: a symlink in `inbox/`
  pointing at `agents/` is refused.

### Elevation

A restricted session that needs more calls `request_elevation` with a reason.
You get a link, valid 10 minutes. Opening it forces a fresh login at your
provider (`prompt=login`, `max_age=0`), and the page only shows Approve if the
ID token proves a second factor used in the last 5 minutes (docs/oidc.md).
Approving gives that one session a grant for 4 hours.

- A prompt-injected session can ask, but it can't approve: that takes your
  second factor, on your device.
- **An elevation link you didn't ask for means the session read something
  that told it to ask.** Read the reason. Deny.
- The request shows a 4-character code in the session and on the approval
  page: approve only if they match. Every conversation on claude.ai (or on
  ChatGPT) is the same OAuth client, so the code is how you tell them apart.
- The grant is a capability the session passes to each tool call, bound to
  the OAuth client and to the session's own login at the server (one
  authorization, kept across token refreshes). Every claude.ai conversation is
  the same client, so the client alone wouldn't do: a grant id used under a
  different connector login (even on the same client), or from another client,
  is refused and logged (`event=elevation result=refused`). A session whose
  login the server can't tell apart can't ask for one. It is not bound to a
  conversation: see Known limits.
- Grants live in memory: a restart drops them all.
- At most 3 requests per client can wait at once, so a session can't flood you
  with links or crowd out your own.

## The gate

`sync/gate.Caddyfile` is the only route from outside to CouchDB and Ignis.

- CouchDB: a request needs `Authorization: Bearer` and a path under `/`, the
  notes database, or its documents, never `_security`. Everything else is a
  403: basic auth, cookies, `_users`, `_config`, `_all_dbs`, Fauxton, other
  databases. So are an encoded slash or backslash anywhere in the URI (Caddy
  matches decoded paths but forwards them raw, so `/notes%2Fevil` would reach
  CouchDB as another database), creating or dropping the database itself,
  writing design documents, and any method LiveSync doesn't use. CouchDB then
  checks the JWT itself. `lab/compose/e2e.sh` checks all of these with
  a real device key.
- Ignis: the calls that create, rename or remove a whole vault are refused,
  in any case and with any number of slashes.

**A device is a database member, not an admin.** LiveSync puts
`"_couchdb.roles": ["_admin"]` in every token it signs; `couchdb.ini` reads
the roles from `sub` instead (`roles_claim_path = sub`), so CouchDB itself
refuses a device's design documents (even inside a `_bulk_docs` body, which
the gate can't read), `_security`, `_config`, `_users`, `_all_dbs` and other
databases. `lab/compose/e2e.sh` step 4b checks it past the gate. The limit:
the device's private key can sign any `sub`, and `sub = "_admin"` is an
admin. A copied token (5 minutes) is a member; a copied key is still the
server. Revoke a lost device's key.

**Keep the gate in the path.** It keeps basic auth (the admin password),
cookies and Fauxton off the internet, and it is the second wall if the role
mapping ever regresses.

## What else is the owner

Two ways in skip the MCP tiers by design, so give them out like your own
login:

- **Push access to the git remote.** A commit lands in the folder. git-sync
  refuses a remote that carries hidden paths (an `.obsidian/plugins/*/main.js`
  is code that Obsidian, and Obsidian in a browser, runs), symlinks or
  submodules, and logs `status=refused reason=unsafe_remote`; everything else
  is merged.
- **The web app.** Ignis is Obsidian: whoever passes oauth2-proxy edits every
  note.

## Secrets

| Secret | Where | If it leaks |
|---|---|---|
| a device's private key | the device (inside its Setup URI, then the plugin's settings) | read and write every note in the notes database (the gate keeps it from dropping the database or touching any other): revoke the device |
| `E2E_PASSPHRASE` | `.env`, every device | decrypt what's in CouchDB (needs the database too) |
| `COUCHDB_ADMIN_PASSWORD` | `.env` | nothing from outside (the gate), everything from inside |
| `AGENT_NOTES_JWT_SIGNING_KEY` | `.env` | mint MCP access tokens for registered clients: rotate it, every client logs in again |
| `AGENT_NOTES_STORAGE_KEY` | `.env` | decrypt stored upstream tokens on the state volume |
| git token or SSH key | `secrets/git/` | push to the notes repo |

Secrets are never put on a command line or in a URL, and the server's audit
lines never contain note content or a grant id.

## Known limits

- **One owner.** The design assumes one person. Several people would need
  per-user policies, which this doesn't have.
- **Ciphertext isn't deniability.** CouchDB holds encrypted notes, but the
  folder, git and the MCP server see plain text. Protect the host.
- **The OIDC provider is in the trust path.** Whoever controls your account
  there controls the MCP server.
- **An enrolled device is a root key.** Its token makes every session on it
  trusted, no approval. Its key is a Keychain item or a mode-600 file, neither
  hardware-backed: any process running as you can read it. Revoke it when the
  machine leaves your hands.
- **Kubernetes shares egress inside the pod.** In Docker Compose only the MCP
  server, git-sync and the token minter can reach the internet; CouchDB, the
  daemon, the gate and Ignis sit on a network with no route out. The Terraform
  module runs them in one pod, so they share its egress rule (`egress_ports`
  to public addresses only).
- **A grant is per connector login, not per conversation.** Nothing in MCP
  ties a request to one conversation: the 2026-07-28 spec dropped protocol
  sessions (state lives in handles the model passes back, which it can also
  leak), OAuth tokens are per account and per server, and claude.ai sends no
  conversation id, so one connector login serves every chat on the account.
  A grant id that leaks into another chat on the same login works there until
  it expires. The code on the approval page and the 4-hour limit are what keep
  it narrow. ChatGPT sends an anonymized conversation id
  (`_meta["openai/session"]`) that a server could bind to; Codex sends a
  thread id (undocumented). An enrolled device doesn't need grants at all.
- **Grants survive a stolen session.** A session that holds a grant keeps it
  for 4 hours; restart the server to drop every grant at once.
- **A trusted reviewer can still write.** The writer rule stops a session from
  promoting its own inbox item. It doesn't stop an elevated session (or an
  enrolled device) from calling `write_note` on `handoffs/`, `agents/` or any
  other note directly: the trusted tier is trusted. Elevate only for what you
  asked for, and end it when done.
- **Git push access bypasses every MCP tier.** A push lands in the folder
  with no tier, no inbox and no writer record, and a clone holds every note,
  `AGENT_NOTES_PRIVATE_PREFIXES` included (those are hidden from restricted
  MCP readers, not from git). Protect the remote: few people and tokens with
  push access, branch rules, and on a self-hosted remote a pre-receive hook
  if you want checks on what lands.
- **Ignis has no login of its own.** oauth2-proxy in front of it is
  mandatory: without it, anyone who reaches the web app edits every note. In
  Docker Compose, `web` and `oauth2-proxy` share the `web` profile and `web`
  depends on `oauth2-proxy`, so starting one starts the other; the Terraform
  module runs them together under `web_enabled` and only exposes port 4180.
  If your own reverse proxy terminates TLS, point the web host at
  `oauth2-proxy:4180`, never at `gate:8081` (that is Ignis with no login).
