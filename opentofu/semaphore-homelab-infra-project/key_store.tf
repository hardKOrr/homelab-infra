# Key store entry: GitHub SSH key
resource "semaphoreui_key" "github" {
  project_id   = semaphoreui_project.homelab.id
  name         = "github"
  type         = "ssh"
  content      = tls_private_key.repo_key.private_key_pem
  description  = "Public Key: ${tls_private_key.repo_key.public_key_openssh}"
  ssh_username = "git"
}

# Key store entry: Semaphore API login/password for the homelab-infra user
resource "semaphoreui_key" "semaphore_api" {
  project_id  = semaphoreui_project.homelab.id
  name        = "semaphore-api"
  type        = "login_password"
  login       = semaphoreui_user.homelab.username
  password    = semaphoreui_api_token.homelab.token
  description = "API token for homelab-infra Semaphore user"
}

# Key store entry: Semaphore user credentials (pre-generated password)
resource "semaphoreui_key" "semaphore_user" {
  project_id  = semaphoreui_project.homelab.id
  name        = "semaphore-user"
  type        = "login_password"
  login       = semaphoreui_user.homelab.username
  password    = random_password.homelab.result
  description = "Login credentials for homelab-infra user"
}
