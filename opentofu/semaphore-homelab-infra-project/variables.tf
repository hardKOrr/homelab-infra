variable "semaphore_url" {
  description = "Semaphore base URL (e.g., https://semaphore.local). The /api suffix is added automatically."
  type        = string
}

variable "semaphore_admin_token" {
  description = "API token for an admin user to bootstrap resources"
  type        = string
  sensitive   = true
}
