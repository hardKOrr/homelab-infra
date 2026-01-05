# Team with homelab user as owner
resource "semaphoreui_team" "homelab" {
  project_id = semaphoreui_project.homelab.id
  name       = "homelab-infra"
  role       = "owner"
  users      = [semaphoreui_user.homelab.id]
}
