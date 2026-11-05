# What your ingress (or this module's, var.ingress) points at. The sync
# Service is the gate, never CouchDB: LiveSync's device tokens are CouchDB
# admins, and the gate is what confines them to the notes database.

resource "kubernetes_service_v1" "sync" {
  metadata {
    name      = "sync"
    namespace = local.ns
    labels    = local.notes_labels
  }
  spec {
    selector = local.notes_labels
    port {
      name        = "sync"
      port        = 5985
      target_port = 5985
    }
  }
}

resource "kubernetes_service_v1" "mcp" {
  metadata {
    name      = "mcp"
    namespace = local.ns
    labels    = local.notes_labels
  }
  spec {
    selector = local.notes_labels
    port {
      name        = "mcp"
      port        = 8000
      target_port = 8000
    }
  }
}

resource "kubernetes_service_v1" "web" {
  count = var.web_enabled ? 1 : 0
  metadata {
    name      = "web"
    namespace = local.ns
    labels    = local.notes_labels
  }
  spec {
    selector = local.notes_labels
    port {
      name        = "web"
      port        = 4180
      target_port = 4180
    }
  }
}

locals {
  routes = merge(
    { sync = { host = var.hosts.sync, service = kubernetes_service_v1.sync.metadata[0].name, port = 5985 } },
    { mcp = { host = var.hosts.mcp, service = kubernetes_service_v1.mcp.metadata[0].name, port = 8000 } },
    var.web_enabled ? { web = { host = var.hosts.web, service = kubernetes_service_v1.web[0].metadata[0].name, port = 4180 } } : {},
  )
}

resource "kubernetes_ingress_v1" "this" {
  for_each = var.ingress.enabled ? local.routes : {}

  metadata {
    name        = each.key
    namespace   = local.ns
    labels      = local.labels
    annotations = var.ingress.annotations
  }
  spec {
    ingress_class_name = var.ingress.class_name
    dynamic "tls" {
      for_each = lookup(var.ingress.tls_secrets, each.key, "") != "" ? [1] : []
      content {
        hosts       = [each.value.host]
        secret_name = var.ingress.tls_secrets[each.key]
      }
    }
    rule {
      host = each.value.host
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = each.value.service
              port {
                number = each.value.port
              }
            }
          }
        }
      }
    }
  }
}
