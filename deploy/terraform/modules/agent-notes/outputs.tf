output "namespace" {
  description = "The namespace everything lives in."
  value       = local.ns
}

output "services" {
  description = "Where to point your own routes when var.ingress is off: host => service:port. The sync route must reach the gate (this service), never CouchDB."
  value       = { for k, r in local.routes : r.host => "${r.service}.${local.ns}.svc:${r.port}" }
}

output "e2e_passphrase" {
  description = "LiveSync's end-to-end passphrase, for scripts/setup-uri (E2E_PASSPHRASE)."
  value       = local.e2e_passphrase
  sensitive   = true
}

output "couchdb_admin_password" {
  description = "CouchDB's admin password: break-glass only, through kubectl port-forward to the couchdb Service."
  value       = random_password.couchdb_admin.result
  sensitive   = true
}

output "oidc_redirect_uris" {
  description = "Redirect URIs to register on the OIDC client."
  value = concat(
    ["https://${var.hosts.mcp}/auth/callback", "https://${var.hosts.mcp}/elevate/callback"],
    var.web_enabled ? ["https://${var.hosts.web}/oauth2/callback"] : [],
  )
}
