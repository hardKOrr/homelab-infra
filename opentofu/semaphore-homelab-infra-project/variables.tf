variable "semaphore_url" {
  description = "Semaphore API base URL (include /api, e.g., http://127.0.0.1:3000/api)."
  type        = string
  default     = "http://127.0.0.1:3000/api"
}

variable "semaphore_admin_token" {
  description = "API token with admin access"
  type        = string
  sensitive   = true
}
