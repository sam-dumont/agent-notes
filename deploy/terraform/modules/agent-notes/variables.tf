# --- Where and what ---------------------------------------------------------

variable "namespace" {
  description = "Namespace to create. Everything the module makes lives in it."
  type        = string
  default     = "agent-notes"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$", var.namespace))
    error_message = "namespace: a DNS label (lowercase letters, digits and -)."
  }
}

variable "name" {
  description = "The MCP server's name (AGENT_NOTES_NAME). Also the audience of device tokens."
  type        = string
  default     = "agent-notes"
}

variable "hosts" {
  description = "Public hostnames: sync (Obsidian's LiveSync plugin), mcp (MCP clients), web (Obsidian in a browser, only with web_enabled)."
  type = object({
    sync = string
    mcp  = string
    web  = optional(string, "")
  })

  validation {
    condition     = alltrue([for h in [var.hosts.sync, var.hosts.mcp] : can(regex("^[a-z0-9.-]+$", h))])
    error_message = "hosts.sync and hosts.mcp: bare hostnames, no scheme or path."
  }
}

variable "node_selector" {
  description = "Node selector for every pod (e.g. keep them on one site)."
  type        = map(string)
  default     = {}
}

variable "storage_class_name" {
  description = "StorageClass for the two volumes (null: the cluster default)."
  type        = string
  default     = null
}

variable "storage" {
  description = "Volume sizes: the notes (folder, git dir, LiveSync's local database, MCP state) and CouchDB."
  type = object({
    notes   = optional(string, "5Gi")
    couchdb = optional(string, "5Gi")
  })
  default = {}
}

# --- OIDC (docs/oidc.md) -----------------------------------------------------

variable "oidc" {
  description = "One confidential OIDC client. Redirect URIs to register: https://<mcp>/auth/callback, https://<mcp>/elevate/callback, and https://<web>/oauth2/callback with web_enabled."
  type = object({
    issuer        = string
    client_id     = string
    audience      = optional(string, "")
    web_client_id = optional(string, "")
  })

  validation {
    condition     = startswith(var.oidc.issuer, "https://")
    error_message = "oidc.issuer must be https."
  }
}

variable "oidc_ca_pem" {
  description = "PEM of a private CA that signs your OIDC provider's certificate (a self-hosted Keycloak, say). When set, the MCP server trusts only this CA."
  type        = string
  default     = ""
}

variable "oidc_client_secret" {
  description = "The OIDC client's secret."
  type        = string
  sensitive   = true
}

variable "oidc_web_client_secret" {
  description = "A separate client secret for the web app's oauth2-proxy (empty: reuse oidc_client_secret)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "allowed_subs" {
  description = "OIDC `sub` values allowed to use the MCP server: yours. Anyone else at the provider is refused."
  type        = list(string)

  validation {
    condition     = length(var.allowed_subs) > 0
    error_message = "allowed_subs: at least one sub, or every account at the provider gets in."
  }
}

variable "step_up" {
  description = "What proves a fresh second factor on the elevation approval page (docs/oidc.md)."
  type = object({
    strong_amr    = optional(list(string), ["mfa", "phr", "phrh"])
    strong_acr    = optional(list(string), [])
    acr_values    = optional(string, "")
    methods_claim = optional(string, "")
  })
  default = {}
}

# --- Access policy (docs/security.md) ----------------------------------------

variable "policy" {
  description = "What a restricted MCP client may touch (lowercase, vault-relative prefixes), and the rules note every client reads first."
  type = object({
    write_prefixes   = optional(list(string), ["inbox/"])
    append_prefixes  = optional(list(string), ["inbox/"])
    private_prefixes = optional(list(string), [])
    trusted_clients  = optional(list(string), [])
    # Off: a loopback redirect can't prove which local process is listening
    # (MCP spec). Trust a machine with mcp_device_keys instead.
    trust_loopback = optional(bool, false)
    rules_path     = optional(string, "agents/digest.md")
  })
  default = {}
}

variable "mcp_device_keys" {
  description = "Enrolled machines trusted without elevation: kid => ES256 public key (PEM). Treat each like a root SSH key (docs/devices.md)."
  type        = map(string)
  default     = {}
}

# --- Sync --------------------------------------------------------------------

variable "devices" {
  description = "LiveSync devices: name => ES256 public key (PEM), from scripts/device-key.sh. Removing one revokes it at the next CouchDB restart (the config checksum rolls it)."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k, v in var.devices : can(regex("^[a-z0-9-]{1,32}$", k)) && startswith(trimspace(v), "-----BEGIN PUBLIC KEY-----")])
    error_message = "devices: names are lowercase letters, digits and - (max 32); values are PEM public keys."
  }
}

variable "couchdb_database" {
  description = "The notes database in CouchDB."
  type        = string
  default     = "notes"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_-]{0,63}$", var.couchdb_database))
    error_message = "couchdb_database: lowercase letters, digits, _ and -, starting with a letter."
  }
}

variable "jwt_sub" {
  description = "The CouchDB user every device token names (`sub`), and member of the notes database."
  type        = string
  default     = "owner"

  validation {
    condition     = can(regex("^[a-z0-9_-]+$", var.jwt_sub))
    error_message = "jwt_sub: lowercase letters, digits, _ and -."
  }
}

variable "e2e_passphrase" {
  description = "LiveSync's end-to-end passphrase, the same on every device. Empty: one is generated (output e2e_passphrase). Never change it on a live setup: every device would stop decrypting."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = var.e2e_passphrase == "" || length(var.e2e_passphrase) >= 16
    error_message = "e2e_passphrase: at least 16 characters."
  }
}

# --- Git (docs/git.md) -------------------------------------------------------

variable "git" {
  description = "The git remote git-sync keeps in step with the folder. Auth: token (HTTPS), ssh_key + ssh_known_hosts, or github_app (short-lived tokens)."
  type = object({
    remote          = string
    branch          = optional(string, "main")
    ssh_known_hosts = optional(string, "")
    token_user      = optional(string, "x-access-token")
    github_app = optional(object({
      id              = string
      installation_id = string
      repo            = string
    }))
  })
}

variable "git_token" {
  description = "HTTPS token for the remote (fine-grained, Contents read/write on that one repo). Empty with ssh_key or github_app."
  type        = string
  sensitive   = true
  default     = ""
}

variable "git_ssh_key" {
  description = "SSH private key for the remote (a deploy key with write access). Needs git.ssh_known_hosts."
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_app_private_key" {
  description = "The GitHub App's private key (PEM), with git.github_app."
  type        = string
  sensitive   = true
  default     = ""
}

# --- Obsidian in a browser ---------------------------------------------------

variable "web_enabled" {
  description = "Run Obsidian in a browser (Ignis) behind oauth2-proxy at hosts.web."
  type        = bool
  default     = false
}

variable "web_allowed_email" {
  description = "The one email oauth2-proxy lets in (Ignis has no login of its own)."
  type        = string
  default     = ""
}

variable "obsidian" {
  description = "The Obsidian release Ignis is built for and its asar.gz SHA-256. Bump with the Ignis image, never alone. (The extractor is pinned by sync/asar/package-lock.json.)"
  type = object({
    version = optional(string, "1.14.4")
    sha256  = optional(string, "d1ed428c363968774f0f3906e67d8a53a058ece0b6cdd924b07865977b2ada21")
  })
  default = {}
}

# --- Exposure ----------------------------------------------------------------

variable "ingress" {
  description = "Optional networking.k8s.io/v1 Ingresses, one per host. Off: point your own routes at the Services (sync: port 5985, mcp: 8000, web: 4180). Either way the sync route must go to the gate (the sync Service), never to CouchDB."
  type = object({
    enabled     = optional(bool, false)
    class_name  = optional(string)
    annotations = optional(map(string), {})
    tls_secrets = optional(map(string), {})
  })
  default = {}
}

variable "ingress_from" {
  description = "Namespace labels of your ingress controller, e.g. { \"kubernetes.io/metadata.name\" = \"ingress-nginx\" }: the only namespace that may reach the gate, the MCP server and the web app."
  type        = map(string)
  validation {
    condition     = length(var.ingress_from) > 0
    error_message = "ingress_from: name your ingress controller's namespace. An empty selector would let every namespace in the cluster reach CouchDB's gate and the MCP server."
  }
}

variable "egress_ports" {
  description = "TCP ports the MCP server, git-sync and the token minter may reach outside the cluster (the OIDC provider, the git remote). Core NetworkPolicy can't name hosts."
  type        = list(number)
  default     = [443, 22]
}

variable "egress_in_cluster" {
  description = "In-cluster services the notes pod may reach: an OIDC provider or a git server running in the cluster. egress_ports only opens addresses outside the cluster's private ranges."
  type = list(object({
    namespace_labels = map(string)
    pod_labels       = optional(map(string), {})
    ports            = list(number)
  }))
  default = []
}

# --- Images and telemetry -----------------------------------------------------

variable "images" {
  description = "Container images. Pin the server image by digest (ghcr.io/sam-dumont/agent-notes-server:main@sha256:...) in production."
  type = object({
    server       = string
    couchdb      = optional(string, "docker.io/library/couchdb:3.5.3@sha256:bed314d10cdf58c0fff63352db4c3f168df58a185a773c0e2295105375c5ef10")
    livesync     = optional(string, "ghcr.io/vrtmrz/livesync-cli:1.0.44-cli@sha256:e54b06dab7f97ad2b262e3f0328f8b44dc86cc0a68f303899d8afeaea1b0fbbe")
    git          = optional(string, "docker.io/alpine/git:2.54.0@sha256:ac6c828f15d82775d204d3d75112e1d34af8945da80ccf922713c9891ba68e7a")
    curl         = optional(string, "docker.io/curlimages/curl:8.23.0@sha256:3fb7e13e5d179a845c97d78204f3f1064d1907037f26a3ebf91710571151fd93")
    caddy        = optional(string, "docker.io/library/caddy:2.11.7@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e")
    ignis        = optional(string, "docker.io/nobbe/ignis:0.8.18@sha256:8faba2774dd9069360aedbf9ace87cdf926c454ccee56a2d7bc5ff7556737567")
    oauth2_proxy = optional(string, "quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916")
  })
  validation {
    # A registry image must be pinned by digest: a moving tag lets whoever can
    # push to it run your MCP server, which holds the OIDC client secret and
    # every note. Local images (kind, a private build) have no registry host.
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.images.server)) || !can(regex("^[^/]+[.:][^/]+/", var.images.server))
    error_message = "images.server: pin a registry image by digest (ghcr.io/sam-dumont/agent-notes-server:0.1.0@sha256:...) and verify it: gh attestation verify oci://<image> -R sam-dumont/agent-notes."
  }
}

variable "otel_endpoint" {
  description = "OTLP/HTTP endpoint for the MCP server's traces and metrics (empty: off)."
  type        = string
  default     = ""
}

variable "sync_dir" {
  description = "Path to the repo's sync/ directory (scripts and templates). Empty: found from the module's own path, which works with a git:: source or a relative local path."
  type        = string
  default     = ""
}
