# Semaphore Job Definitions

Import `project.json` into Semaphore to get all job templates pre-configured.

## Jobs

| Job | Playbook | Parameters |
|---|---|---|
| Bootstrap Platform | `playbooks/bootstrap.yml` | none |
| Deploy App | `playbooks/apps/<app>.yml` | `instance` (app instance name) |
| Remove App | `playbooks/apps/remove.yml` | `instance` (app instance name) |
| Wire Stack | `playbooks/stacks/wire-<stack>.yml` | `stack` (stack name) |
| Rollback Container | `playbooks/stacks/rollback-container.yml` | `stack`, `container`, `image_tag` |
| Lab Status | `playbooks/maintenance/status.yml` | none |
| Check Native Updates | `playbooks/maintenance/check-native-updates.yml` | none (also scheduled weekly) |

## Environment Variables (set in Semaphore secrets)

- `PROXMOX_API_TOKEN` — Proxmox API token secret
- `VAULTWARDEN_ADMIN_TOKEN` — Vaultwarden admin token (set after bootstrap step 1)

The `community.proxmox` dynamic inventory plugin cannot receive its connection details via `-e`
extra vars (see `ansible/inventory/proxmox.yml`). Export `PROXMOX_API_HOST` / `PROXMOX_API_PORT` /
`PROXMOX_API_USER` / `PROXMOX_API_TOKEN_ID` / `PROXMOX_API_TOKEN_SECRET` from Semaphore secrets in
the job step before invoking `ansible`, or wrap the invocation in
`ansible/scripts/with-proxmox-env.sh <user-vars.yml> <ansible-command>...`.

## TODO: project.json
Semaphore project export with all job templates, inventory, and environment pre-configured.
