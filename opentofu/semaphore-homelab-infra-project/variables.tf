variable "semaphore_url" {
  description = "Semaphore base URL (e.g., https://semaphore.local)"
  type        = string
}

variable "semaphore_admin_token" {
  description = "API token for an admin user to bootstrap resources"
  type        = string
  sensitive   = true
}