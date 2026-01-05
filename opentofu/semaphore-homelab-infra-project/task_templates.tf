# Task template to update this project using OpenTofu code
resource "semaphoreui_project_template" "update_project" {
  name           = "Update Homelab Infra Semaphore Project"
  description    = "Apply OpenTofu config to keep Semaphore project in sync"
  project_id     = semaphoreui_project.homelab_infra.id
  repository_id  = semaphoreui_project_repository.homelab_infra.id
  inventory_id   = semaphoreui_project_inventory.homelab_infra_workspace.id
  environment_id = null
  app            = "tofu"
  playbook       = "opentofu/semaphore-homelab-infra-project"
  arguments      = []
}
