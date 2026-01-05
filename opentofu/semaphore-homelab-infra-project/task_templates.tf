# Task template to update this project using OpenTofu code
resource "semaphoreui_task_template" "update_project" {
  project_id     = semaphoreui_project.homelab.id
  name           = "Update Homelab Infra Semaphore Project"
  repository_id  = semaphoreui_repository.homelab.id
  playbook       = "opentofu/semaphore-homelab-infra-project"
  type           = "terraform"
  environment_id = null
  inventory_id   = null
  vault_id       = null
  key_id         = semaphoreui_key.github.id
  auto_sync      = true
}
