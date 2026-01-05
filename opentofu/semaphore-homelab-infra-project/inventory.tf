resource "semaphoreui_project_inventory" "homelab_infra_workspace" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Homelab Infra Project Workspace"
  ssh_key_id = semaphoreui_project_key.none.id

  terraform_workspace = {
    workspace = "homelab-infra-project"
  }
}
