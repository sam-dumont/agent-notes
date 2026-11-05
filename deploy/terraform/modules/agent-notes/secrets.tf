# Generated once and kept in Terraform state: protect the state like the
# secrets themselves (encrypted backend, restricted access).

resource "random_id" "checksum_salt" {
  byte_length = 32
}

resource "random_id" "couchdb_uuid" {
  byte_length = 16
}

resource "random_password" "couchdb_admin" {
  length  = 40
  special = false
}

resource "random_password" "couchdb_cookie" {
  length  = 40
  special = false
}

# base64url only: couchdb-setup.sh builds JSON from it and refuses anything else.
resource "random_password" "livesync" {
  length  = 40
  special = false
}

resource "random_password" "e2e" {
  length  = 40
  special = false
}

resource "random_password" "jwt_signing" {
  length  = 48
  special = false
}

resource "random_bytes" "storage_key" {
  length = 32
}

resource "random_password" "oauth2_cookie" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "couchdb" {
  metadata {
    name      = "couchdb"
    namespace = local.ns
    labels    = local.labels
  }
  data = {
    COUCHDB_USER     = "admin"
    COUCHDB_PASSWORD = random_password.couchdb_admin.result
    COUCHDB_SECRET   = random_password.couchdb_cookie.result
  }
}

resource "kubernetes_secret_v1" "setup" {
  metadata {
    name      = "couchdb-setup"
    namespace = local.ns
    labels    = local.labels
  }
  data = {
    COUCHDB_USER      = "admin"
    COUCHDB_PASSWORD  = random_password.couchdb_admin.result
    LIVESYNC_PASSWORD = random_password.livesync.result
  }
}

resource "kubernetes_secret_v1" "livesync" {
  metadata {
    name      = "livesync-settings"
    namespace = local.ns
    labels    = local.labels
  }
  data = {
    "settings.json" = local.livesync_settings
  }
}

resource "kubernetes_secret_v1" "mcp" {
  metadata {
    name      = "mcp"
    namespace = local.ns
    labels    = local.labels
  }
  data = {
    OIDC_CLIENT_SECRET          = var.oidc_client_secret
    AGENT_NOTES_JWT_SIGNING_KEY = random_password.jwt_signing.result
    AGENT_NOTES_STORAGE_KEY     = local.storage_key
  }
}

# Mounted as files in git-sync (and the token minter): never in env, never
# in the remote URL.
resource "kubernetes_secret_v1" "git" {
  metadata {
    name      = "git"
    namespace = local.ns
    labels    = local.labels
  }
  data = merge(
    local.git_mode == "token" ? { token = var.git_token } : {},
    local.git_mode == "ssh" ? { ssh_key = var.git_ssh_key, known_hosts = var.git.ssh_known_hosts } : {},
    local.git_mode == "github-app" ? { "github-app.pem" = var.github_app_private_key } : {},
  )
}

resource "kubernetes_secret_v1" "web" {
  count = var.web_enabled ? 1 : 0
  metadata {
    name      = "web"
    namespace = local.ns
    labels    = local.labels
  }
  data = {
    OAUTH2_PROXY_CLIENT_SECRET = var.oidc_web_client_secret != "" ? var.oidc_web_client_secret : var.oidc_client_secret
    OAUTH2_PROXY_COOKIE_SECRET = random_password.oauth2_cookie.result
    emails                     = var.web_allowed_email
  }
}
