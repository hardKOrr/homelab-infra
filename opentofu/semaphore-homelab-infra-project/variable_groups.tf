resource "semaphoreui_project_environment" "bootstrap" {
  project_id = semaphoreui_project.homelab.id
  name       = "bootstrap"

  environment = {
    TF_VAR_semaphore_url = local.semaphore_api_url
  }

  secrets = [{
    name  = "TF_VAR_semaphore_admin_token"
    type  = "env"
    value = var.semaphore_admin_token
  }]
}
