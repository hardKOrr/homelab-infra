resource "semaphoreui_project_inventory" "tofu_workspace" {
  project_id = semaphoreui_project.homelab.id
  name       = "tofu-workspace"
  ssh_key_id = semaphoreui_project_key.none.id

  terraform_workspace = {
    workspace = "default"
  }
}
