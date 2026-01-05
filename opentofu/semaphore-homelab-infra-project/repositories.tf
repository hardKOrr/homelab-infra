# Repository hooked to this project
resource "semaphoreui_project_repository" "homelab_infra" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Github hardKOrr/homelab-infra"
  url        = "git@github.com:hardKOrr/homelab-infra.git"
  branch     = "master"
  ssh_key_id = semaphoreui_project_key.github.id
}
