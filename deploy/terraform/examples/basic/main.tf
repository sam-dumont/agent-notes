# A minimal root module: agent-notes on the cluster your kubeconfig points
# at. Copy terraform.tfvars.example to terraform.tfvars (never commit it),
# fill it in, then terraform init && terraform apply.

terraform {
  required_version = ">= 1.6"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }
  # Keep state in an encrypted backend: it holds the generated secrets.
}

provider "kubernetes" {
  config_path    = var.kubeconfig
  config_context = var.kube_context
}

variable "kubeconfig" {
  type    = string
  default = "~/.kube/config"
}

variable "kube_context" {
  type = string
}

variable "hosts" {
  type = object({ sync = string, mcp = string, web = optional(string, "") })
}

variable "oidc" {
  type = object({ issuer = string, client_id = string, audience = optional(string, "") })
}

variable "oidc_client_secret" {
  type      = string
  sensitive = true
}

variable "allowed_subs" {
  type = list(string)
}

variable "git_remote" {
  type = string
}

variable "git_token" {
  type      = string
  sensitive = true
}

variable "server_image" {
  type        = string
  description = "The server image, pinned by digest: ghcr.io/sam-dumont/agent-notes-server:<version>@sha256:<digest>, the digest from the package page (github.com/sam-dumont/agent-notes/pkgs/container/agent-notes-server), verified first (see images below)"
}

variable "devices" {
  type    = map(string)
  default = {}
}

module "agent_notes" {
  # From GitHub: source = "git::https://github.com/sam-dumont/agent-notes.git//deploy/terraform/modules/agent-notes?ref=<tag>"
  source = "../../modules/agent-notes"

  hosts              = var.hosts
  oidc               = var.oidc
  oidc_client_secret = var.oidc_client_secret
  allowed_subs       = var.allowed_subs
  git                = { remote = var.git_remote }
  git_token          = var.git_token
  devices            = var.devices
  ingress_from       = { "kubernetes.io/metadata.name" = "ingress-nginx" }
  # A release pinned by digest; verify it first:
  #   gh attestation verify oci://ghcr.io/sam-dumont/agent-notes-server@sha256:<digest> -R sam-dumont/agent-notes
  images = { server = var.server_image }

  # Your ingress controller: one Ingress per host, TLS from cert-manager.
  ingress = {
    enabled     = true
    class_name  = "nginx"
    annotations = { "cert-manager.io/cluster-issuer" = "letsencrypt" }
    tls_secrets = { sync = "sync-tls", mcp = "mcp-tls" }
  }
}

output "services" {
  value = module.agent_notes.services
}

output "oidc_redirect_uris" {
  value = module.agent_notes.oidc_redirect_uris
}
