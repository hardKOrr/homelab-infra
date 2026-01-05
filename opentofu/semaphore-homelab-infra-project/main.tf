terraform {
  required_version = ">= 1.6.0"

  required_providers {
    semaphoreui = {
      source  = "semaphoreui/semaphoreui"
      version = ">= 0.6.0"
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
  url   = var.semaphore_url
  token = var.semaphore_admin_token
}

# SSH keypair for Git access in Semaphore
resource "tls_private_key" "repo_key" {
  algorithm = "ED25519"
}

# Password for homelab-infra user
resource "random_password" "homelab" {
  length           = 24
  special          = false
}

# Admin user for homelab-infra automation
resource "semaphoreui_user" "homelab" {
  name     = "homelab-infra"
  username = "homelab-infra"
  email    = "homelab-infra@localhost"
  password = random_password.homelab.result
  admin    = true
}

# API token for the homelab-infra user
resource "semaphoreui_api_token" "homelab" {
  user_id = semaphoreui_user.homelab.id
  name    = "homelab-infra-bootstrap"
}

# Project definition
resource "semaphoreui_project" "homelab" {
  name = "Homelab Infra"
}
