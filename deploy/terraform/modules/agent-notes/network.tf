# Default deny, then exactly what each piece needs. Core NetworkPolicy can't
# name hosts, so egress to the OIDC provider and the git remote is by port
# (var.egress_ports) to anything outside the cluster's private ranges.

locals {
  private_ranges = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"]
  exposed_ports  = concat([5985, 8000], var.web_enabled ? [4180] : [])
}

resource "kubernetes_network_policy_v1" "default_deny" {
  metadata {
    name      = "default-deny"
    namespace = local.ns
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy_v1" "dns" {
  metadata {
    name      = "dns"
    namespace = local.ns
  }
  spec {
    pod_selector {}
    policy_types = ["Egress"]
    egress {
      to {
        namespace_selector {}
      }
      ports {
        port     = 53
        protocol = "UDP"
      }
      ports {
        port     = 53
        protocol = "TCP"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "couchdb" {
  metadata {
    name      = "couchdb"
    namespace = local.ns
  }
  spec {
    pod_selector {
      match_labels = local.couchdb_labels
    }
    policy_types = ["Ingress"]
    ingress {
      from {
        pod_selector {
          match_labels = local.notes_labels
        }
      }
      from {
        pod_selector {
          match_labels = local.setup_labels
        }
      }
      ports {
        port     = 5984
        protocol = "TCP"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "setup" {
  metadata {
    name      = "couchdb-setup"
    namespace = local.ns
  }
  spec {
    pod_selector {
      match_labels = local.setup_labels
    }
    policy_types = ["Egress"]
    egress {
      to {
        pod_selector {
          match_labels = local.couchdb_labels
        }
      }
      ports {
        port     = 5984
        protocol = "TCP"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "notes" {
  metadata {
    name      = "notes"
    namespace = local.ns
  }
  spec {
    pod_selector {
      match_labels = local.notes_labels
    }
    policy_types = ["Ingress", "Egress"]

    # From the ingress controller: the gate, the MCP server, the web app.
    ingress {
      from {
        namespace_selector {
          match_labels = var.ingress_from
        }
      }
      dynamic "ports" {
        for_each = local.exposed_ports
        content {
          port     = ports.value
          protocol = "TCP"
        }
      }
    }

    egress {
      to {
        pod_selector {
          match_labels = local.couchdb_labels
        }
      }
      ports {
        port     = 5984
        protocol = "TCP"
      }
    }

    # The OIDC provider, the git remote, GitHub's API.
    egress {
      to {
        ip_block {
          cidr   = "0.0.0.0/0"
          except = local.private_ranges
        }
      }
      dynamic "ports" {
        for_each = var.egress_ports
        content {
          port     = ports.value
          protocol = "TCP"
        }
      }
    }

    # An OIDC provider or git server inside the cluster (Keycloak, Gitea...):
    # private addresses are excluded above, so each one is named here.
    dynamic "egress" {
      for_each = var.egress_in_cluster
      content {
        to {
          namespace_selector {
            match_labels = egress.value.namespace_labels
          }
          pod_selector {
            match_labels = egress.value.pod_labels
          }
        }
        dynamic "ports" {
          for_each = egress.value.ports
          content {
            port     = ports.value
            protocol = "TCP"
          }
        }
      }
    }
  }
}
