terraform {
  required_version = ">= 1.6.0"

  required_providers {
    semaphoreui = {
      source  = "semaphoreui/semaphore"
      version = "~> 0.1"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0.0"
    }
  }
}

provider "semaphoreui" {
  api_base_url = var.semaphore_url
  api_token    = var.semaphore_admin_token
}

# SSH keypair for Git access in Semaphore
resource "tls_private_key" "repo_key" {
  algorithm = "ED25519"
}

# Project definition
resource "semaphoreui_project" "homelab" {
  name = "Homelab Infra"
}
