# Repository hooked to this project
resource "semaphoreui_repository" "homelab" {
  project_id = semaphoreui_project.homelab.id
  name       = "homelab-infra"
  git_url    = "git@github.com:hardKOrr/homelab-infra.git"
  branch     = "master"
  key_id     = semaphoreui_key.github.id
}
