# Key used when no credentials are required
resource "semaphoreui_project_key" "none" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "None"
  none       = {}
}

# Key store entry: Semaphore admin API token for reuse
resource "semaphoreui_project_key" "semaphore_api" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Semaphore Admin API Token"
  login_password = {
    login    = "api-token"
    password = var.semaphore_admin_token
  }
}

# Key store entry: GitHub SSH key
resource "semaphoreui_project_key" "github" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Semaphore Homelab Infra GitHub SSH Key"
  ssh = {
    login       = "git"
    private_key = tls_private_key.repo_key.private_key_pem
  }
}
