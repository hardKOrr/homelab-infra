resource "semaphoreui_project_environment" "homelab_infra_variable_group" {
  project_id = semaphoreui_project.homelab_infra.id
  name       = "Homelab Infra OpenTofu Environment"
}
