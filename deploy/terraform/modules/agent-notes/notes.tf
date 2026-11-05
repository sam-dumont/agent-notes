# The notes pod: one volume, every piece that touches the folder.
#
#   livesync      CouchDB <-> /vault/notes (the Obsidian plugin's sync core)
#   git-sync      /vault/notes <-> the git remote, every 30 s
#   gh-app-token  optional: a 1-hour GitHub App token for git-sync
#   mcp           the MCP server
#   gate          the only way in from outside to CouchDB (:5985) and the web app (:8081)
#   web           optional: Obsidian in a browser (Ignis), behind
#   oauth2-proxy  optional: SSO in front of the web app (:4180)
#
# One replica, Recreate: the volume is ReadWriteOnce and the daemon, git-sync
# and the MCP server's grants assume a single writer.

locals {
  # The same container hardening everywhere (PSA restricted).
  web_on     = var.web_enabled
  gh_app_on  = local.git_mode == "github-app"
  token_file = local.git_mode == "token" ? "/secrets/git/token" : local.gh_app_on ? "/run/git/token" : ""
  # git-sync.sh makes its own private copy of an SSH key (a Secret file gets the pod's group).
  git_sync_sh = "exec /bin/sh /scripts/git-sync.sh"

  git_env = merge(
    {
      GIT_DIR             = "/vault/git"
      GIT_WORK_TREE       = "/vault/notes"
      NOTES_REMOTE        = var.git.remote
      NOTES_BRANCH        = var.git.branch
      GIT_TOKEN_USER      = var.git.token_user
      HOME                = "/tmp"
      GIT_AUTHOR_NAME     = "agent-notes"
      GIT_AUTHOR_EMAIL    = "agent-notes@localhost"
      GIT_COMMITTER_NAME  = "agent-notes"
      GIT_COMMITTER_EMAIL = "agent-notes@localhost"
    },
    local.token_file != "" ? { GIT_TOKEN_FILE = local.token_file } : {},
    # ssh refuses a group-readable key, and Secret files get the pod's fsGroup:
    # git-sync copies it to /tmp with mode 600 first (local.git_sync_sh).
    local.git_mode == "ssh" ? { GIT_SSH_KEY = "/secrets/git/ssh_key", GIT_SSH_KNOWN_HOSTS = "/secrets/git/known_hosts" } : {},
  )
}

resource "kubernetes_deployment_v1" "notes" {
  metadata {
    name      = "notes"
    namespace = local.ns
    labels    = local.notes_labels
  }

  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = local.notes_labels
    }

    template {
      metadata {
        labels = local.notes_labels
        annotations = {
          # Config changes roll the pod (secrets as hashes, never in clear).
          "checksum/files"    = sha1(jsonencode(local.scripts))
          "checksum/livesync" = sha1(local.livesync_settings)
          "checksum/mcp"      = sha1(jsonencode([local.mcp_env, var.oidc_ca_pem]))
          # Salted: anyone who can read pods sees annotations, and a plain hash
          # of a short token could be guessed offline.
          "checksum/git" = sha256(jsonencode([random_id.checksum_salt.hex, local.git_env, var.git_token, var.git_ssh_key, var.github_app_private_key]))
        }
      }

      spec {
        node_selector                   = var.node_selector
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000
          run_as_non_root = true
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        # The folders on the volume, owned by 1000 through fsGroup.
        init_container {
          name    = "prepare"
          image   = var.images.git
          command = ["/bin/sh", "-c", "mkdir -p /vault/shared/notes /vault/shared/tmp /vault/git /vault/livesync /vault/state /vault/ignis && chmod 700 /vault/git /vault/livesync /vault/state /vault/ignis"]
          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
          resources {
            requests = { cpu = "5m", memory = "8Mi" }
            limits   = { cpu = "100m", memory = "32Mi" }
          }
          volume_mount {
            name       = "vault"
            mount_path = "/vault"
          }
        }

        # Obsidian's app bundle for Ignis, from Obsidian's releases, checked by SHA-256.
        dynamic "init_container" {
          for_each = local.web_on ? [1] : []
          content {
            name    = "obsidian-fetch"
            image   = var.images.ignis
            command = ["/bin/sh", "/scripts/obsidian-fetch.sh"]
            env {
              name  = "OBSIDIAN_VERSION"
              value = var.obsidian.version
            }
            env {
              name  = "OBSIDIAN_SHA256"
              value = var.obsidian.sha256
            }
            env {
              name  = "ASAR_PACKAGE"
              value = "/scripts/asar-package.json"
            }
            env {
              name  = "ASAR_LOCK"
              value = "/scripts/asar-package-lock.json"
            }
            env {
              name  = "HOME"
              value = "/tmp"
            }
            env {
              name  = "npm_config_cache"
              value = "/tmp/npm"
            }
            security_context {
              read_only_root_filesystem  = true
              run_as_non_root            = true
              allow_privilege_escalation = false
              capabilities {
                drop = ["ALL"]
              }
            }
            resources {
              requests = { cpu = "50m", memory = "128Mi" }
              limits   = { cpu = "1", memory = "512Mi" }
            }
            volume_mount {
              name       = "obsidian-app"
              mount_path = "/app/obsidian-app"
            }
            volume_mount {
              name       = "files"
              mount_path = "/scripts/obsidian-fetch.sh"
              sub_path   = "obsidian-fetch.sh"
              read_only  = true
            }
            volume_mount {
              name       = "tmp-fetch"
              mount_path = "/tmp"
            }
          }
        }

        container {
          name              = "livesync"
          image             = var.images.livesync
          image_pull_policy = "IfNotPresent"
          args              = ["--settings", "/etc/livesync/settings.json", "--vault", "/vault/notes", "daemon"]
          env {
            name  = "LIVESYNC_DB_PATH"
            value = "/vault/livesync"
          }
          env {
            name  = "HOME"
            value = "/tmp"
          }
          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
          resources {
            requests = { cpu = "10m", memory = "160Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
          # Only its part of the volume: never the git dir or the MCP state.
          volume_mount {
            name       = "vault"
            mount_path = "/vault/notes"
            sub_path   = "shared/notes"
          }
          volume_mount {
            name       = "vault"
            mount_path = "/vault/livesync"
            sub_path   = "livesync"
          }
          volume_mount {
            name       = "livesync-settings"
            mount_path = "/etc/livesync"
            read_only  = true
          }
          volume_mount {
            name       = "tmp-livesync"
            mount_path = "/tmp"
          }
        }

        container {
          name              = "git-sync"
          image             = var.images.git
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c", local.git_sync_sh]
          dynamic "env" {
            for_each = local.git_env
            content {
              name  = env.key
              value = env.value
            }
          }
          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
          resources {
            requests = { cpu = "5m", memory = "32Mi" }
            limits   = { cpu = "500m", memory = "256Mi" }
          }
          volume_mount {
            name       = "vault"
            mount_path = "/vault/notes"
            sub_path   = "shared/notes"
          }
          volume_mount {
            name       = "vault"
            mount_path = "/vault/git"
            sub_path   = "git"
          }
          volume_mount {
            name       = "files"
            mount_path = "/scripts/git-sync.sh"
            sub_path   = "git-sync.sh"
            read_only  = true
          }
          # ssh refuses to run for a uid with no passwd entry (alpine/git has none for 1000).
          volume_mount {
            name       = "files"
            mount_path = "/etc/passwd"
            sub_path   = "git-sync.passwd"
            read_only  = true
          }
          volume_mount {
            name       = "git-secrets"
            mount_path = "/secrets/git"
            read_only  = true
          }
          volume_mount {
            name       = "gh-token"
            mount_path = "/run/git"
            read_only  = true
          }
          volume_mount {
            name       = "tmp-git"
            mount_path = "/tmp"
          }
        }

        # Mints the 1-hour installation token git-sync pushes with. Only this
        # container reads the App's private key.
        dynamic "container" {
          for_each = local.gh_app_on ? [1] : []
          content {
            name              = "gh-app-token"
            image             = var.images.server
            image_pull_policy = "IfNotPresent"
            command           = ["python", "/app/gh_app_token.py"]
            env {
              name  = "GH_APP_ID"
              value = var.git.github_app.id
            }
            env {
              name  = "GH_APP_INSTALLATION_ID"
              value = var.git.github_app.installation_id
            }
            env {
              name  = "GH_APP_REPO"
              value = var.git.github_app.repo
            }
            env {
              name  = "GH_APP_KEY_FILE"
              value = "/secrets/git/github-app.pem"
            }
            env {
              name  = "TOKEN_FILE"
              value = "/run/git/token"
            }
            security_context {
              read_only_root_filesystem  = true
              run_as_non_root            = true
              allow_privilege_escalation = false
              capabilities {
                drop = ["ALL"]
              }
            }
            resources {
              requests = { cpu = "5m", memory = "32Mi" }
              limits   = { cpu = "200m", memory = "128Mi" }
            }
            volume_mount {
              name       = "git-secrets"
              mount_path = "/secrets/git"
              read_only  = true
            }
            volume_mount {
              name       = "gh-token"
              mount_path = "/run/git"
            }
          }
        }

        container {
          name              = "mcp"
          image             = var.images.server
          image_pull_policy = "IfNotPresent"
          dynamic "env" {
            for_each = { for k, v in local.mcp_env : k => v if v != "" }
            content {
              name  = env.key
              value = env.value
            }
          }
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.mcp.metadata[0].name
            }
          }
          port {
            name           = "mcp"
            container_port = 8000
          }
          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
          readiness_probe {
            http_get {
              path = "/healthz"
              port = "mcp"
            }
            period_seconds = 15
          }
          liveness_probe {
            http_get {
              path = "/healthz"
              port = "mcp"
            }
            initial_delay_seconds = 20
            period_seconds        = 30
            failure_threshold     = 4
          }
          resources {
            requests = { cpu = "10m", memory = "96Mi" }
            limits   = { cpu = "500m", memory = "384Mi" }
          }
          # notes and tmp share one mount (writes are a rename from tmp into
          # notes); the state is separate; no git dir, no daemon database.
          volume_mount {
            name       = "vault"
            mount_path = "/vault"
            sub_path   = "shared"
          }
          volume_mount {
            name       = "vault"
            mount_path = "/state"
            sub_path   = "state"
          }
          volume_mount {
            name       = "tmp-mcp"
            mount_path = "/tmp"
          }
          dynamic "volume_mount" {
            for_each = var.oidc_ca_pem != "" ? [1] : []
            content {
              name       = "files"
              mount_path = "/etc/agent-notes/oidc-ca.pem"
              sub_path   = "oidc-ca.pem"
              read_only  = true
            }
          }
        }

        container {
          name              = "gate"
          image             = var.images.caddy
          image_pull_policy = "IfNotPresent"
          command           = ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
          env {
            name  = "COUCHDB_DATABASE"
            value = var.couchdb_database
          }
          env {
            name  = "COUCHDB_UPSTREAM"
            value = "couchdb.${local.ns}.svc:5984"
          }
          env {
            name  = "IGNIS_UPSTREAM"
            value = "127.0.0.1:8080"
          }
          env {
            name  = "XDG_DATA_HOME"
            value = "/tmp"
          }
          env {
            name  = "XDG_CONFIG_HOME"
            value = "/tmp"
          }
          port {
            name           = "sync"
            container_port = 5985
          }
          security_context {
            read_only_root_filesystem  = true
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
              # The caddy binary carries the net_bind_service file capability:
              # without it in the bounding set, exec fails. It binds no
              # privileged port here. The only capability PSA restricted allows.
              add = ["NET_BIND_SERVICE"]
            }
          }
          readiness_probe {
            tcp_socket {
              port = "sync"
            }
            period_seconds = 15
          }
          resources {
            requests = { cpu = "5m", memory = "24Mi" }
            limits   = { cpu = "300m", memory = "128Mi" }
          }
          volume_mount {
            name       = "files"
            mount_path = "/etc/caddy/Caddyfile"
            sub_path   = "Caddyfile"
            read_only  = true
          }
          volume_mount {
            name       = "tmp-gate"
            mount_path = "/tmp"
          }
        }

        dynamic "container" {
          for_each = local.web_on ? [1] : []
          content {
            name              = "web"
            image             = var.images.ignis
            image_pull_policy = "IfNotPresent"
            # Skips the image's entrypoint (root: useradd, chown, gosu) and
            # Obsidian Sync's headless client, which this setup doesn't use.
            command = ["node", "/app/apps/ignis-server/server/index.js"]
            dynamic "env" {
              for_each = {
                VAULT_ROOT           = "/vaults"
                DATA_ROOT            = "/app/data"
                OBSIDIAN_ASSETS_PATH = "/app/obsidian-app"
                PORT                 = "8080"
                HOME                 = "/tmp"
                WS_ORIGINS           = "https://${var.hosts.web}"
              }
              content {
                name  = env.key
                value = env.value
              }
            }
            security_context {
              read_only_root_filesystem  = true
              run_as_non_root            = true
              allow_privilege_escalation = false
              capabilities {
                drop = ["ALL"]
              }
            }
            resources {
              requests = { cpu = "10m", memory = "160Mi" }
              limits   = { cpu = "500m", memory = "512Mi" }
            }
            volume_mount {
              name       = "vault"
              mount_path = "/vaults/notes"
              sub_path   = "shared/notes"
            }
            volume_mount {
              name       = "vault"
              mount_path = "/app/data"
              sub_path   = "ignis"
            }
            volume_mount {
              name       = "obsidian-app"
              mount_path = "/app/obsidian-app"
              read_only  = true
            }
            volume_mount {
              name       = "tmp-web"
              mount_path = "/tmp"
            }
          }
        }

        dynamic "container" {
          for_each = local.web_on ? [1] : []
          content {
            name              = "oauth2-proxy"
            image             = var.images.oauth2_proxy
            image_pull_policy = "IfNotPresent"
            dynamic "env" {
              for_each = {
                OAUTH2_PROXY_PROVIDER        = "oidc"
                OAUTH2_PROXY_OIDC_ISSUER_URL = var.oidc.issuer
                OAUTH2_PROXY_CLIENT_ID       = var.oidc.web_client_id != "" ? var.oidc.web_client_id : var.oidc.client_id
                OAUTH2_PROXY_REDIRECT_URL    = "https://${var.hosts.web}/oauth2/callback"
                OAUTH2_PROXY_COOKIE_SECURE   = "true"
                OAUTH2_PROXY_COOKIE_SAMESITE = "lax"
                # No EMAIL_DOMAINS: "*" there admits every account at the
                # provider and the emails file is never consulted.
                OAUTH2_PROXY_AUTHENTICATED_EMAILS_FILE = "/etc/oauth2-proxy/emails"
                OAUTH2_PROXY_UPSTREAMS                 = "http://127.0.0.1:8081/"
                OAUTH2_PROXY_HTTP_ADDRESS              = "0.0.0.0:4180"
                OAUTH2_PROXY_REVERSE_PROXY             = "true"
                OAUTH2_PROXY_SKIP_PROVIDER_BUTTON      = "true"
                OAUTH2_PROXY_CODE_CHALLENGE_METHOD     = "S256"
              }
              content {
                name  = env.key
                value = env.value
              }
            }
            env {
              name = "OAUTH2_PROXY_CLIENT_SECRET"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.web[0].metadata[0].name
                  key  = "OAUTH2_PROXY_CLIENT_SECRET"
                }
              }
            }
            env {
              name = "OAUTH2_PROXY_COOKIE_SECRET"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.web[0].metadata[0].name
                  key  = "OAUTH2_PROXY_COOKIE_SECRET"
                }
              }
            }
            port {
              name           = "web"
              container_port = 4180
            }
            security_context {
              run_as_user                = 65532
              run_as_group               = 65532
              read_only_root_filesystem  = true
              run_as_non_root            = true
              allow_privilege_escalation = false
              capabilities {
                drop = ["ALL"]
              }
            }
            resources {
              requests = { cpu = "5m", memory = "24Mi" }
              limits   = { cpu = "200m", memory = "128Mi" }
            }
            volume_mount {
              name       = "web-secrets"
              mount_path = "/etc/oauth2-proxy/emails"
              sub_path   = "emails"
              read_only  = true
            }
          }
        }

        volume {
          name = "vault"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.notes.metadata[0].name
          }
        }
        volume {
          name = "files"
          config_map {
            name = kubernetes_config_map_v1.files.metadata[0].name
          }
        }
        volume {
          name = "livesync-settings"
          secret {
            secret_name  = kubernetes_secret_v1.livesync.metadata[0].name
            default_mode = "0440"
          }
        }
        volume {
          name = "git-secrets"
          secret {
            secret_name  = kubernetes_secret_v1.git.metadata[0].name
            default_mode = "0440"
          }
        }
        dynamic "volume" {
          for_each = local.web_on ? [1] : []
          content {
            name = "web-secrets"
            secret {
              secret_name  = kubernetes_secret_v1.web[0].metadata[0].name
              default_mode = "0444"
              items {
                key  = "emails"
                path = "emails"
              }
            }
          }
        }
        volume {
          name = "gh-token"
          empty_dir {
            medium     = "Memory"
            size_limit = "1Mi"
          }
        }
        dynamic "volume" {
          for_each = local.web_on ? ["obsidian-app", "tmp-fetch", "tmp-web"] : []
          content {
            name = volume.value
            empty_dir {
              size_limit = volume.value == "obsidian-app" ? "512Mi" : "256Mi"
            }
          }
        }
        dynamic "volume" {
          for_each = ["tmp-livesync", "tmp-git", "tmp-mcp", "tmp-gate"]
          content {
            name = volume.value
            empty_dir {
              size_limit = "64Mi"
            }
          }
        }
      }
    }
  }

  wait_for_rollout = false
}
