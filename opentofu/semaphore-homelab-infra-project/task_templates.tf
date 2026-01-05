# Task template to update this project using OpenTofu code
resource "semaphoreui_project_template" "update_project" {
  name           = "Update Homelab Infra Semaphore Project"
  description    = "Apply OpenTofu config to keep Semaphore project in sync"
  project_id     = semaphoreui_project.homelab.id
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.semaphore_workspace.id
  environment_id = semaphoreui_project_environment.semaphore.id
  app            = "tofu"
  playbook       = "opentofu/semaphore-homelab-infra-project"
  arguments      = ["apply", "-auto-approve"]
}
