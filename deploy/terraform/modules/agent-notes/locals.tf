locals {
  ns = kubernetes_namespace_v1.this.metadata[0].name

  # The repo's sync/ directory: the same scripts and templates as Docker
  # Compose. A git:: source (the whole repo is fetched) or a relative path
  # keeps it four levels up; an absolute local path makes Terraform copy only
  # this directory, so set var.sync_dir then.
  sync_dir = var.sync_dir != "" ? var.sync_dir : "${path.module}/../../../../sync"

  git_mode = (
    var.git.github_app != null ? "github-app" :
    var.git_ssh_key != "" ? "ssh" :
    var.git_token != "" ? "token" : "none"
  )

  # Exactly what server/render_config.py writes for Docker Compose: one line
  # per device, sorted by name, the PEM's newlines escaped.
  jwt_keys = join("\n", [
    for kid in sort(keys(var.devices)) : "ec:${kid} = ${replace(trimspace(var.devices[kid]), "\n", "\\n")}"
  ])
  couchdb_ini = templatefile("${local.sync_dir}/couchdb.ini.tmpl", {
    uuid     = random_id.couchdb_uuid.hex
    database = var.couchdb_database
    jwt_keys = local.jwt_keys
  })

  e2e_passphrase = var.e2e_passphrase != "" ? var.e2e_passphrase : random_password.e2e.result

  # The daemon's settings; the CLI fills in the rest with its defaults.
  # encrypt and usePathObfuscation must match the devices' Setup URIs.
  livesync_settings = jsonencode({
    couchDB_URI        = "http://couchdb.${local.ns}.svc:5984"
    couchDB_USER       = "livesync"
    couchDB_PASSWORD   = random_password.livesync.result
    couchDB_DBNAME     = var.couchdb_database
    encrypt            = true
    passphrase         = local.e2e_passphrase
    usePathObfuscation = true
    liveSync           = true
    syncOnStart        = true
    syncOnSave         = true
    isConfigured       = true
  })

  # Fernet wants url-safe base64 of 32 random bytes, padded.
  storage_key = replace(replace(random_bytes.storage_key.base64, "+", "-"), "/", "_")

  mcp_env = {
    # Only the provider's private CA when one is given: the MCP server talks
    # to nothing else outside the pod.
    SSL_CERT_FILE                = var.oidc_ca_pem != "" ? "/etc/agent-notes/oidc-ca.pem" : ""
    AGENT_NOTES_NAME             = var.name
    AGENT_NOTES_BASE_URL         = "https://${var.hosts.mcp}"
    AGENT_NOTES_ROOT             = "/vault/notes"
    AGENT_NOTES_TMP              = "/vault/tmp"
    AGENT_NOTES_STATE_DIR        = "/state"
    AGENT_NOTES_ALLOWED_SUBS     = join(",", var.allowed_subs)
    AGENT_NOTES_WRITE_PREFIXES   = join(",", var.policy.write_prefixes)
    AGENT_NOTES_APPEND_PREFIXES  = join(",", var.policy.append_prefixes)
    AGENT_NOTES_PRIVATE_PREFIXES = join(",", var.policy.private_prefixes)
    AGENT_NOTES_TRUSTED_CLIENTS  = join(",", var.policy.trusted_clients)
    AGENT_NOTES_TRUST_LOOPBACK   = var.policy.trust_loopback ? "1" : ""
    AGENT_NOTES_RULES_PATH       = var.policy.rules_path
    AGENT_NOTES_DEVICE_KEYS      = length(var.mcp_device_keys) > 0 ? jsonencode(var.mcp_device_keys) : ""
    AGENT_NOTES_STRONG_AMR       = join(",", var.step_up.strong_amr)
    AGENT_NOTES_STRONG_ACR       = join(",", var.step_up.strong_acr)
    AGENT_NOTES_ACR_VALUES       = var.step_up.acr_values
    AGENT_NOTES_METHODS_CLAIM    = var.step_up.methods_claim
    OIDC_ISSUER                  = var.oidc.issuer
    OIDC_CLIENT_ID               = var.oidc.client_id
    OIDC_AUDIENCE                = var.oidc.audience
    OTEL_SERVICE_NAME            = var.name
    OTEL_EXPORTER_OTLP_ENDPOINT  = var.otel_endpoint
  }

  labels = {
    "app.kubernetes.io/part-of"    = "agent-notes"
    "app.kubernetes.io/managed-by" = "terraform"
  }
  notes_labels   = merge(local.labels, { "app.kubernetes.io/name" = "notes" })
  couchdb_labels = merge(local.labels, { "app.kubernetes.io/name" = "couchdb" })
  setup_labels   = merge(local.labels, { "app.kubernetes.io/name" = "couchdb-setup" })

  # PSA restricted, and the same hardening as deploy/compose on every container.
  scripts = {
    "git-sync.sh"       = file("${local.sync_dir}/git-sync.sh")
    "git-sync.passwd"   = file("${local.sync_dir}/git-sync.passwd")
    "couchdb-setup.sh"  = file("${local.sync_dir}/couchdb-setup.sh")
    "obsidian-fetch.sh" = file("${local.sync_dir}/obsidian-fetch.sh")
    "Caddyfile"         = file("${local.sync_dir}/gate.Caddyfile")
    # The asar extractor's pinned lockfile (sync/asar), for obsidian-fetch.
    "asar-package.json"      = file("${local.sync_dir}/asar/package.json")
    "asar-package-lock.json" = file("${local.sync_dir}/asar/package-lock.json")
  }
}
