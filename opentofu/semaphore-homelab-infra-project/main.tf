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

locals {
  semaphore_base_url = trimsuffix(var.semaphore_url, "/")
  semaphore_api_url  = endswith(local.semaphore_base_url, "/api") ? local.semaphore_base_url : "${local.semaphore_base_url}/api"
}

provider "semaphoreui" {
  api_base_url = local.semaphore_api_url
  api_token    = var.semaphore_admin_token
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

# Project definition
resource "semaphoreui_project" "homelab" {
  name = "Homelab Infra"
}
