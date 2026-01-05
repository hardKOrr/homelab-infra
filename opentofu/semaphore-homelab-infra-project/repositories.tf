# Repository hooked to this project
resource "semaphoreui_project_repository" "homelab" {
  project_id = semaphoreui_project.homelab.id
  name       = "homelab-infra"
  url        = "git@github.com:hardKOrr/homelab-infra.git"
  branch     = "master"
  ssh_key_id = semaphoreui_project_key.github.id
}
