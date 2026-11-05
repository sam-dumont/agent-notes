# CouchDB: LiveSync's server. Reached from outside only through the gate (the
# notes pod), from inside only by the notes pod and the setup Job.

resource "kubernetes_deployment_v1" "couchdb" {
  metadata {
    name      = "couchdb"
    namespace = local.ns
    labels    = local.couchdb_labels
  }

  spec {
    replicas = 1
    strategy {
      type = "Recreate"
    }
    selector {
      match_labels = local.couchdb_labels
    }

    template {
      metadata {
        labels = local.couchdb_labels
        annotations = {
          # A new device key (or a removed one) restarts CouchDB with it.
          "checksum/config" = sha1(local.couchdb_ini)
        }
      }

      spec {
        node_selector                   = var.node_selector
        automount_service_account_token = false
        enable_service_links            = false

        # The image's entrypoint only re-owns files and drops to `couchdb` with
        # gosu when it starts as root. As uid 5984 it skips that, writes the
        # admin hash and cookie secret into local.d (an emptyDir) and starts.
        security_context {
          run_as_user     = 5984
          run_as_group    = 5984
          fs_group        = 5984
          run_as_non_root = true
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "couchdb"
          image             = var.images.couchdb
          image_pull_policy = "IfNotPresent"

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.couchdb.metadata[0].name
            }
          }
          # Erlang sizes its port table from the open-file limit; where that
          # limit is huge (kind's containerd: ~1e9) CouchDB is OOM-killed at
          # boot whatever the memory limit. 65536 ports is plenty.
          env {
            name  = "ERL_FLAGS"
            value = "+Q 65536"
          }

          port {
            name           = "http"
            container_port = 5984
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
              path = "/_up"
              port = "http"
            }
            period_seconds = 15
          }
          liveness_probe {
            http_get {
              path = "/_up"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 30
            failure_threshold     = 4
          }

          resources {
            requests = { cpu = "20m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }

          volume_mount {
            name       = "data"
            mount_path = "/opt/couchdb/data"
          }
          volume_mount {
            name       = "files"
            mount_path = "/opt/couchdb/etc/default.d/10-agent-notes.ini"
            sub_path   = "couchdb.ini"
            read_only  = true
          }
          volume_mount {
            name       = "local-d"
            mount_path = "/opt/couchdb/etc/local.d"
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.couchdb.metadata[0].name
          }
        }
        volume {
          name = "files"
          config_map {
            name = kubernetes_config_map_v1.files.metadata[0].name
          }
        }
        volume {
          name = "local-d"
          empty_dir {
            medium     = "Memory"
            size_limit = "1Mi"
          }
        }
        volume {
          name = "tmp"
          empty_dir {
            size_limit = "64Mi"
          }
        }
      }
    }
  }
  wait_for_rollout = false
}

resource "kubernetes_service_v1" "couchdb" {
  metadata {
    name      = "couchdb"
    namespace = local.ns
    labels    = local.couchdb_labels
  }
  spec {
    selector = local.couchdb_labels
    port {
      name        = "http"
      port        = 5984
      target_port = "http"
    }
  }
}

# The notes database, the daemon's user, the database's members. Idempotent;
# the name carries a hash of the script and its inputs, so a change reruns it.
resource "kubernetes_job_v1" "couchdb_setup" {
  metadata {
    name = "couchdb-setup-${substr(sha1(join("\n", [
      local.scripts["couchdb-setup.sh"], var.couchdb_database, var.jwt_sub, random_password.livesync.result,
    ])), 0, 8)}"
    namespace = local.ns
    labels    = local.setup_labels
  }

  spec {
    backoff_limit              = 6
    ttl_seconds_after_finished = 86400

    template {
      metadata {
        labels = local.setup_labels
      }
      spec {
        restart_policy                  = "OnFailure"
        node_selector                   = var.node_selector
        automount_service_account_token = false
        enable_service_links            = false

        security_context {
          run_as_user     = 100
          run_as_group    = 101
          run_as_non_root = true
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name    = "setup"
          image   = var.images.curl
          command = ["/bin/sh", "/scripts/couchdb-setup.sh"]

          env {
            name  = "COUCH"
            value = "http://couchdb.${local.ns}.svc:5984"
          }
          env {
            name  = "COUCHDB_DATABASE"
            value = var.couchdb_database
          }
          env {
            name  = "JWT_SUB"
            value = var.jwt_sub
          }
          env {
            name  = "TMPDIR"
            value = "/tmp"
          }
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.setup.metadata[0].name
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
            requests = { cpu = "5m", memory = "16Mi" }
            limits   = { cpu = "200m", memory = "64Mi" }
          }

          volume_mount {
            name       = "files"
            mount_path = "/scripts/couchdb-setup.sh"
            sub_path   = "couchdb-setup.sh"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "files"
          config_map {
            name = kubernetes_config_map_v1.files.metadata[0].name
          }
        }
        volume {
          name = "tmp"
          empty_dir {
            size_limit = "8Mi"
          }
        }
      }
    }
  }

  wait_for_completion = true
  timeouts {
    create = "10m"
  }
}
