output "homelab_user_api_token" {
  description = "API token for the homelab-infra user"
  value       = semaphoreui_api_token.homelab.token
  sensitive   = true
}

output "github_private_key" {
  description = "Private key for GitHub access (store in Semaphore key store)"
  value       = tls_private_key.repo_key.private_key_pem
  sensitive   = true
}

output "github_public_key" {
  description = "Public key to add to GitHub deploy keys"
  value       = tls_private_key.repo_key.public_key_openssh
}

output "homelab_user_password" {
  description = "Generated password for homelab-infra user"
  value       = random_password.homelab.result
  sensitive   = true
}
