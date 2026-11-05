resource "kubernetes_namespace_v1" "this" {
  metadata {
    name = var.namespace
    # Everything here passes PSA restricted: enforce it.
    labels = merge(local.labels, {
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
      "pod-security.kubernetes.io/warn"    = "restricted"
    })
  }
}

# Scripts, the gate's Caddyfile and CouchDB's ini: no secret in any of them
# (the ini holds public keys and a uuid).
resource "kubernetes_config_map_v1" "files" {
  metadata {
    name      = "agent-notes-files"
    namespace = local.ns
    labels    = local.labels
  }
  data = merge(local.scripts, { "couchdb.ini" = local.couchdb_ini }, var.oidc_ca_pem != "" ? { "oidc-ca.pem" = var.oidc_ca_pem } : {})
}

resource "kubernetes_persistent_volume_claim_v1" "notes" {
  metadata {
    name      = "notes"
    namespace = local.ns
    labels    = local.labels
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name
    resources {
      requests = { storage = var.storage.notes }
    }
  }
  wait_until_bound = false
}

resource "kubernetes_persistent_volume_claim_v1" "couchdb" {
  metadata {
    name      = "couchdb"
    namespace = local.ns
    labels    = local.labels
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name
    resources {
      requests = { storage = var.storage.couchdb }
    }
  }
  wait_until_bound = false
}
