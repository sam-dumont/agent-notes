# agent-notes on Kubernetes

A Terraform module (`modules/agent-notes`) that deploys the same stack as
`deploy/compose` on any Kubernetes cluster, plus an example root
(`examples/basic`). Providers: `hashicorp/kubernetes` 3.4.0 and
`hashicorp/random` 3.9.1, pinned. No CRDs, no `kubernetes_manifest`, nothing
tied to one ingress controller.

## What it creates

One namespace (PSA `restricted`, enforced) with:

- **CouchDB**: one replica, its own volume, the ini rendered from
  `sync/couchdb.ini.tmpl` with your device keys. Runs as uid 5984 straight
  away, so the image's entrypoint skips its root-only steps.
- **A setup Job**: the notes database, the daemon's user, the members. Its
  name carries a hash of the script and its inputs, so a change reruns it.
- **The notes pod**, one replica, `Recreate`, one volume:
  - `livesync`: CouchDB to the folder, the Obsidian plugin's own sync core
  - `git-sync`: the folder to your git remote every 30 s
  - `gh-app-token` (with `git.github_app`): 1-hour GitHub App tokens
  - `mcp`: the MCP server
  - `gate`: Caddy with `sync/gate.Caddyfile`, the only way in to CouchDB and the web app
  - `web` + `oauth2-proxy` (with `web_enabled`): Obsidian in a browser behind SSO
- **Services** `sync` (5985, the gate), `mcp` (8000) and `web` (4180).
- **Ingresses**, one per host, only with `ingress.enabled` (any class).
- **NetworkPolicies**: default deny, DNS, CouchDB only from the notes pod and
  the setup Job, and egress from the notes pod on `egress_ports` (443 and 22)
  to anything outside the private ranges (the OIDC provider, the git remote).
- **Secrets** generated with `random` and kept in Secrets, never in a
  ConfigMap or a command line. They live in the Terraform state too: use an
  encrypted backend.

Every container runs as non-root with a read-only root filesystem, no
capabilities (the gate keeps `NET_BIND_SERVICE`, which Caddy's binary needs
to exec at all), no privilege escalation, `RuntimeDefault` seccomp and no
service account token. Config changes roll the pods through checksum
annotations.

## The gate rule

LiveSync's device tokens carry CouchDB's `_admin` role (the plugin hard-codes
it). CouchDB ignores it (`roles_claim_path = sub`): a device is a member of
the notes database, nothing more. The gate keeps it there too: only Bearer
requests to the notes database get through, never `_users`, `_config`,
`_security` or Fauxton. Whatever routes traffic to this namespace (this
module's Ingresses or your own), **the sync host must point at the `sync`
Service, never at `couchdb`**.

## Use it

```hcl
module "agent_notes" {
  source = "git::https://github.com/sam-dumont/agent-notes.git//deploy/terraform/modules/agent-notes?ref=<tag>"

  hosts              = { sync = "sync.example.com", mcp = "notes-mcp.example.com" }
  oidc               = { issuer = "https://idp.example.com", client_id = "agent-notes" }
  oidc_client_secret = var.oidc_client_secret
  allowed_subs       = ["your-sub"]
  git                = { remote = "https://github.com/you/notes.git" }
  git_token          = var.git_token
  devices            = { phone = file("devices/phone.pub") }
  ingress            = { enabled = true, class_name = "nginx" }
}
```

Use a `git::` source or a relative path: either keeps the repo's `sync/`
directory where the module expects it. With an absolute local path Terraform
copies only the module directory; set `sync_dir` then.

Inputs worth knowing (all in `variables.tf`, with descriptions):

| Input | Default | What |
|---|---|---|
| `hosts` | | `sync`, `mcp`, `web` hostnames |
| `oidc`, `oidc_client_secret` | | one confidential client; `oidc_redirect_uris` output lists what to register |
| `allowed_subs` | | your `sub`, the only account the MCP server serves |
| `step_up` | amr `mfa`, `phr`, `phrh` | what proves a fresh second factor on the approval page |
| `policy` | inbox/, handoffs/ | what restricted clients may write |
| `git`, `git_token` / `git_ssh_key` / `github_app_private_key` | | the remote and one way to authenticate |
| `devices` | `{}` | LiveSync devices: name => public key |
| `mcp_device_keys` | `{}` | machines trusted without elevation |
| `web_enabled`, `web_allowed_email` | off | Obsidian in a browser |
| `ingress`, `ingress_from` | off, any namespace | your ingress class, annotations, TLS secrets |
| `images` | pinned digests | pin the server image by digest too |

Outputs: `services`, `oidc_redirect_uris`, and (sensitive) `e2e_passphrase`
and `couchdb_admin_password`.

## Add or revoke a device

```sh
scripts/device-key.sh phone          # devices/phone.pub, secrets/devices/phone.key (600)
```

Add `phone = file("devices/phone.pub")` to `devices` and apply: the ini
changes, its checksum rolls CouchDB with the new key. Then build the
device's Setup URI with `E2E_PASSPHRASE` from `terraform output -raw
e2e_passphrase` (`scripts/setup-uri`). To revoke, delete the line and apply.

## Upgrades

- **Images**: every default is pinned by digest. Bump LiveSync (the CLI) with
  the plugin on your devices; bump Ignis and `obsidian` together, never alone.
- **Providers**: pinned exactly in `versions.tf`.
- **Never change** `e2e_passphrase` or the generated CouchDB uuid on a live
  setup: every device would stop decrypting, or replicate from scratch.
- Back up the notes volume or rely on the git remote: the folder rebuilds
  from it, and CouchDB rebuilds from the folder.
