resource "semaphoreui_project_environment" "semaphore" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Homelab Infra OpenTofu Environment"

  environment = {
    SEMAPHOREUI_API_BASE_URL = var.semaphore_url
  }

  secrets = [{
    name  = "SEMAPHOREUI_API_TOKEN"
    type  = "env"
    value = var.semaphore_admin_token
  }]
}
