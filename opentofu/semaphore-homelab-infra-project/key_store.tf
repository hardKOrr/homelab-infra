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

# Key store entry: Semaphore user credentials (pre-generated password)
resource "semaphoreui_project_key" "semaphore_user" {
  project_id = semaphoreui_project.homelab.id
  name       = "semaphore-user"
  login_password = {
    login    = semaphoreui_user.homelab.username
    password = random_password.homelab.result
  }
}
