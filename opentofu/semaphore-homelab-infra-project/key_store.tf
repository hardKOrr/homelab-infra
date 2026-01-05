# Key used when no credentials are required
resource "semaphoreui_project_key" "none" {
  project_id = semaphoreui_project.homelab.id
  name       = "none"
  none       = {}
}

# Key store entry: GitHub SSH key
resource "semaphoreui_project_key" "github" {
  project_id = semaphoreui_project.homelab.id
  name       = "github"
  ssh = {
    login       = "git"
    private_key = tls_private_key.repo_key.private_key_pem
  }
}
