resource "semaphoreui_project_environment" "semaphore" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Homelab Infra OpenTofu Environment"

  environment = {
    TF_VAR_semaphore_url = var.semaphore_url
  }

  secrets = [{
    name  = "TF_VAR_semaphore_admin_token"
    type  = "env"
    value = var.semaphore_admin_token
  }]
}
