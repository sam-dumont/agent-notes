# The lab

Everything in this folder exists so the whole system can run end to end on one
machine (or in CI) without any account anywhere. None of it is meant for real
use: each piece stands in for something you bring yourself.

```bash
lab/compose/e2e.sh                 # the Docker Compose stack, ~3 minutes
E2E_WEB=1 lab/compose/e2e.sh       # plus Obsidian in a browser (downloads Obsidian, ~100 MB)
lab/kind/run.sh                    # the Terraform module on a throwaway kind cluster, ~6 minutes
KEEP=1 lab/compose/e2e.sh          # leave the stack running afterwards, to look around
```

Needs Docker (Compose v2.30+), Node 22+, OpenSSL and [uv](https://docs.astral.sh/uv/); the kind run
also needs kind, Terraform and kubectl. CI runs all three on every push.

## Core vs lab

| | The core (deploy it) | The lab (replace it) |
|---|---|---|
| MCP server, sync, gate | `server/`, `sync/`, `deploy/compose/compose.yaml`, `deploy/terraform/modules/agent-notes/` | — |
| OIDC provider | your Auth0, Keycloak, Authentik, Google... (`docs/oidc.md`) | `ghcr.io/navikt/mock-oauth2-server`: logs anyone in; its login form takes the token's claims, which is how the test plays a password login and a passkey login |
| Git host | GitHub, Gitea, Forgejo, GitLab... with a private repo (`docs/git.md`) | `git-server`: Alpine with openssh and git-shell, one deploy key, the host key pinned |
| TLS | Let's Encrypt (the `edge` profile) or your own reverse proxy | Caddy's internal CA, trusted inside the containers only |
| DNS | three real hostnames | `sync.test`, `mcp.test`, `idp.test`, `web.test` as Docker network aliases |
| Obsidian on a phone | the Self-hosted LiveSync plugin on your devices | the LiveSync CLI (same sync core), set up from a real Setup URI |
| An MCP client | claude.ai, ChatGPT, Claude Code | `compose/oauth_flow.py`: registers, logs in, gets restricted, elevates |
| Kubernetes | your cluster | `kind`, created and deleted by `kind/run.sh` |
| The inbox review | a trusted session, or a CI job in your notes repo (`reviewer/`) | the session tools in `compose/oauth_flow.py`; the hourly job offline in `reviewer/test_inbox_review.py` |

## What the runs prove

`compose/e2e.sh`, through the real paths:

1. A note pushed to the git remote reaches the folder and the phone; a note the phone writes reaches
   the folder and the git remote; a note an MCP tool writes reaches both.
2. The gate refuses everything but device tokens on the notes database: no auth, basic auth,
   `_users`, `_security`, `_config`, encoded slashes, dropping or recreating the database, design
   documents, `COPY`; a forged token is refused by CouchDB itself.
3. A client registered with a localhost redirect can't finish its login on claude.ai's callback.
4. SSO in front of Obsidian in a browser refuses any other account (with `E2E_WEB=1`: the allowed
   account gets the app, and vault-admin calls are refused).
5. git-sync refuses a remote carrying an Obsidian plugin and a symlink.
6. An MCP client logs in through the consent page and the provider, is restricted (reads the rules,
   can't write them, writes the inbox), and gets elevated: a login without a second factor is
   refused, a fresh one approves, and the grant writes the rules.

`kind/run.sh`: the module applies under PSA `restricted`, the setup Job completes, every pod is
Ready, sync works both ways, the gate refuses what it should, a real device key reads the database.

## Not covered

A real phone running Obsidian, real certificates, a hosted identity provider, the Claude Code hooks
inside Claude Code, and the inbox review in a real notes repo. Those need your accounts: the first
real deployment is where they get tested.
