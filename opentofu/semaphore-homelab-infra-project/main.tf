terraform {
  backend "local" {
    path = "/var/lib/semaphore/tofu-state/semaphore-homelab-infra-project.tfstate"
  }
  
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
}

# SSH keypair for Git access in Semaphore
resource "tls_private_key" "repo_key" {
  algorithm = "ED25519"
}

# Project definition
resource "semaphoreui_project" "homelab_infra" {
  name = "Homelab Infra"
}
