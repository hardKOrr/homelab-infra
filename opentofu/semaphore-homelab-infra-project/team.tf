# Assign homelab user as project owner
resource "semaphoreui_project_user" "homelab_owner" {
  project_id = semaphoreui_project.homelab.id
  user_id    = semaphoreui_user.homelab.id
  role       = "owner"
}
