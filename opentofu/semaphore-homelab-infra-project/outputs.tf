output "github_private_key" {
  description = "Private key for GitHub access (store in Semaphore key store)"
  value       = tls_private_key.repo_key.private_key_pem
  sensitive   = true
}

output "github_public_key" {
  description = "Public key to add to GitHub deploy keys"
  value       = tls_private_key.repo_key.public_key_openssh
}
