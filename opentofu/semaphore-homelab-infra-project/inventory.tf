resource "semaphoreui_project_inventory" "semaphore_workspace" {
  project_id = semaphoreui_project.homelab.id
  name       = "Semaphore OpenTofu Workspace"
  ssh_key_id = semaphoreui_project_key.none.id

  terraform_workspace = {
    workspace = "semaphore"
  }
}
