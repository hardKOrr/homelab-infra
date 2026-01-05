# Homelab Infra — Ansible Semaphore bootstrap
=============================================

This repo is the source for the **Homelab Infra** project in your self-hosted Ansible Semaphore. It holds the scripts and docs for getting the project created via the Semaphore API.

Getting started
- Prereq: Semaphore instance is already up with an admin account (Step 0).
- Create a Semaphore API token: in Semaphore UI go to **Admin → API Tokens → New token**, copy the value.
- Clone the repo:
  ```
  git clone https://github.com/hardKOrr/homelab-infra.git
  cd homelab-infra
  ```
- Export environment variables instead of using an env file. Example:
  ```bash
  export SEMAPHOREUI_API_KEY="<your-admin-api-token>"
  export SEMAPHORE_URL="https://your-semaphore.example/api"
  ```
- Run the bootstrap script to create/configure the project via API:
  ```
  bootstrap/semaphore-project.sh
  ```
  (Make sure it is executable: `chmod +x bootstrap/semaphore-project.sh`.)

IMPORTANT: The bootstrap MUST be run locally against your Semaphore instance (the machine where Semaphore's API and runner access will read state). Running the OpenTofu/Tofu apply locally without sharing state with Semaphore can create duplicate resources: ensure the bootstrap uses the same remote state backend or run it on the Semaphore host so the created state is visible to Semaphore task runs.

OpenTofu automation (optional)
- `opentofu/semaphore-homelab-infra-project/` contains a Tofu config (using the `semaphoreui` provider) that:
  - Creates a `homelab-infra` admin user and API token.
  - Creates the `Homelab Infra` project, SSH key (`github`), API key store entry (`semaphore-api`), repository pointing to this GitHub repo, team ownership, and a task template to update itself.
  - Generates an SSH keypair; outputs the public key for GitHub deploy keys and the user API token.
- To use it, set `semaphore_url` and `semaphore_admin_token` via environment variables (the bootstrap and the provider read env). Example:
  ```bash
  export SEMAPHOREUI_API_KEY="<your-admin-api-token>"
  export SEMAPHORE_URL="https://your-semaphore.example/api"
  export TF_VAR_semaphore_url="$SEMAPHORE_URL"
  export TF_VAR_semaphore_admin_token="$SEMAPHOREUI_API_KEY"
  tofu init && tofu apply -auto-approve
  ```
  Review the outputs to add the public key to GitHub.

State and backend notes:
- If you want both local bootstrap and Semaphore task runs to share the same Terraform/OpenTofu state, configure a remote backend (Terraform Cloud, S3+DynamoDB, GCS, etc.) and ensure both runs use the same backend config and workspace name (example workspace: `semaphore`).
- Alternatively, run the bootstrap on the Semaphore host and enable `TOFU_MIGRATE=true` with backend variables set so the script will migrate local state into the configured backend.
