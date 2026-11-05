# Kind test of the module (run.sh): a mock OIDC provider and a git daemon in a
# "fixtures" namespace, then the module, as close to a real cluster as a laptop
# gets. Never for production.

terraform {
  required_version = ">= 1.6"
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes", version = "3.4.0" }
    random     = { source = "hashicorp/random", version = "3.9.1" }
    tls        = { source = "hashicorp/tls", version = "4.1.0" }
  }
}

variable "kubeconfig" { type = string }
variable "devices" { type = map(string) }
variable "server_image" { type = string }

provider "kubernetes" {
  config_path = var.kubeconfig
}

resource "kubernetes_namespace_v1" "fixtures" {
  metadata {
    name   = "fixtures"
    labels = { "agent-notes-test" = "fixtures" }
  }
}

# Mock OIDC provider: any login is the owner, with amr=mfa.
resource "kubernetes_deployment_v1" "idp" {
  metadata {
    name      = "idp"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector { match_labels = { app = "idp" } }
    template {
      metadata { labels = { app = "idp" } }
      spec {
        container {
          name  = "idp"
          image = "ghcr.io/navikt/mock-oauth2-server:6.0.8@sha256:3a20ed622fdcd0d316cbb9868ebf5df10ae89440f6c136a89f23a3adccf99dbb"
          env {
            name  = "SERVER_PORT"
            value = "8080"
          }
          env {
            name  = "JSON_CONFIG"
            value = jsonencode({ interactiveLogin = false, httpServer = "NettyWrapper", tokenCallbacks = [{ issuerId = "default", tokenExpiry = 3600, requestMappings = [{ requestParam = "grant_type", match = "*", claims = { sub = "owner-sub", aud = ["agent-notes-test"], amr = ["mfa"] } }] }] })
          }
          readiness_probe {
            http_get {
              path = "/default/.well-known/openid-configuration"
              port = 8080
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "idp_backend" {
  metadata {
    name      = "idp-backend"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector = { app = "idp" }
    port {
      port        = 8080
      target_port = 8080
    }
  }
}

# A git daemon serving one bare repo, seeded with a rules note, push enabled.
resource "kubernetes_deployment_v1" "git" {
  metadata {
    name      = "git"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector { match_labels = { app = "git" } }
    template {
      metadata { labels = { app = "git" } }
      spec {
        container {
          name = "git"
          # alpine/git has no `git daemon`; Alpine ships it as git-daemon.
          image   = "docker.io/library/alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -e
            apk add --no-cache -q git git-daemon
            git init -q --bare -b main /srv/notes.git
            cd /tmp && git init -q -b main seed && cd seed
            mkdir -p agents && printf '# Rules\nBe brief.\n' > agents/digest.md && printf '# Index\n' > index.md
            git -c user.name=seed -c user.email=seed@test add -A && git -c user.name=seed -c user.email=seed@test commit -qm seed
            git push -q /srv/notes.git main
            git config --file /srv/notes.git/config daemon.receivepack true
            exec git daemon --reuseaddr --export-all --enable=receive-pack --base-path=/srv /srv
          EOT
          ]
          env {
            name  = "HOME"
            value = "/tmp"
          }
          readiness_probe {
            tcp_socket { port = 9418 }
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "git" {
  metadata {
    name      = "git"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector = { app = "git" }
    port {
      port        = 9418
      target_port = 9418
    }
  }
}

# TLS in front of the mock provider, signed by a throwaway CA, as a
# self-hosted provider with a private CA would be.
resource "tls_private_key" "ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "ca" {
  private_key_pem       = tls_private_key.ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 24
  allowed_uses          = ["cert_signing", "crl_signing"]
  subject { common_name = "agent-notes test CA" }
}

resource "tls_private_key" "idp" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "idp" {
  private_key_pem = tls_private_key.idp.private_key_pem
  dns_names       = ["idp.fixtures.svc"]
  subject { common_name = "idp.fixtures.svc" }
}

resource "tls_locally_signed_cert" "idp" {
  cert_request_pem      = tls_cert_request.idp.cert_request_pem
  ca_private_key_pem    = tls_private_key.ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca.cert_pem
  validity_period_hours = 24
  allowed_uses          = ["digital_signature", "server_auth"]
}

resource "kubernetes_secret_v1" "idp_tls" {
  metadata {
    name      = "idp-tls"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  type = "kubernetes.io/tls"
  data = {
    "tls.crt" = tls_locally_signed_cert.idp.cert_pem
    "tls.key" = tls_private_key.idp.private_key_pem
  }
}

resource "kubernetes_config_map_v1" "idp_caddy" {
  metadata {
    name      = "idp-caddy"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  data = {
    Caddyfile = <<-EOT
      {
        admin off
        auto_https off
      }
      https://idp.fixtures.svc:443 {
        tls /tls/tls.crt /tls/tls.key
        reverse_proxy idp-backend:8080
      }
    EOT
  }
}

resource "kubernetes_deployment_v1" "idp_tls" {
  metadata {
    name      = "idp-tls"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector { match_labels = { app = "idp-tls" } }
    template {
      metadata { labels = { app = "idp-tls" } }
      spec {
        container {
          name  = "caddy"
          image = "docker.io/library/caddy:2.11.7@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e"
          volume_mount {
            name       = "conf"
            mount_path = "/etc/caddy"
          }
          volume_mount {
            name       = "tls"
            mount_path = "/tls"
          }
          readiness_probe {
            tcp_socket { port = 443 }
          }
        }
        volume {
          name = "conf"
          config_map { name = kubernetes_config_map_v1.idp_caddy.metadata[0].name }
        }
        volume {
          name = "tls"
          secret { secret_name = kubernetes_secret_v1.idp_tls.metadata[0].name }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "idp" {
  metadata {
    name      = "idp"
    namespace = kubernetes_namespace_v1.fixtures.metadata[0].name
  }
  spec {
    selector = { app = "idp-tls" }
    port {
      port        = 443
      target_port = 443
    }
  }
}

module "agent_notes" {
  source     = "../../deploy/terraform/modules/agent-notes"
  depends_on = [kubernetes_deployment_v1.idp, kubernetes_deployment_v1.idp_tls, kubernetes_deployment_v1.git]

  namespace          = "notes-test"
  hosts              = { sync = "sync.test", mcp = "mcp.test" }
  oidc               = { issuer = "https://idp.fixtures.svc/default", client_id = "agent-notes-test" }
  oidc_ca_pem        = tls_self_signed_cert.ca.cert_pem
  oidc_client_secret = "test-secret"
  allowed_subs       = ["owner-sub"]
  git                = { remote = "git://git.fixtures.svc/notes.git" }
  devices            = var.devices
  images             = { server = var.server_image }
  ingress_from       = { "kubernetes.io/metadata.name" = "ingress-nginx" }
  egress_in_cluster = [{
    namespace_labels = { "agent-notes-test" = "fixtures" }
    ports            = [443, 9418]
  }]
}
