# Task template to update this project using OpenTofu code
resource "semaphoreui_project_template" "update_project" {
  project_id     = semaphoreui_project.homelab.id
  name           = "Update Homelab Infra Semaphore Project"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.tofu_workspace.id
  environment_id = semaphoreui_project_environment.bootstrap.id
  app            = "tofu"
  playbook       = "opentofu/semaphore-homelab-infra-project"
  description    = "Apply OpenTofu config to keep Semaphore project in sync"
  arguments      = ["apply", "-auto-approve"]
}
