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
- Copy and edit env: `cp example.env .env` then set `SEMAPHORE_URL` and `SEMAPHORE_TOKEN` with your instance URL and the token you copied.
- Run the bootstrap script to create/configure the project via API:
  ```
  bootstrap/semaphore-project.sh
  ```
  (Make sure it is executable: `chmod +x bootstrap/semaphore-project.sh`.)

OpenTofu automation (optional)
- `opentofu/semaphore-homelab-infra-project/` contains a Tofu config (using the `semaphoreui` provider) that:
  - Creates a `homelab-infra` admin user and API token.
  - Creates the `Homelab Infra` project, SSH key (`github`), API key store entry (`semaphore-api`), repository pointing to this GitHub repo, team ownership, and a task template to update itself.
  - Generates an SSH keypair; outputs the public key for GitHub deploy keys and the user API token.
- To use it, set `semaphore_url` and `semaphore_admin_token` (admin API token) via `tofu.tfvars` or env (`TF_VAR_...`), then run `tofu init && tofu apply` from that folder. Review the outputs to add the public key to GitHub.
