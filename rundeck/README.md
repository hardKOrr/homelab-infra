# Rundeck Job Definitions

Import YAML files from `rundeck/jobs/` into Rundeck to get all job definitions pre-configured.

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

## Key Variables (Rundeck Key Storage)

- `keys/proxmox/api-token` — Proxmox API token secret
- `keys/vaultwarden/admin-token` — Vaultwarden admin token (set after bootstrap step 1)

## TODO: jobs/*.yaml
Rundeck job definition YAML files — one per job, importable via Rundeck API or UI.
