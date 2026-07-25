# Semaphore Job Definitions

`project.json` is a Semaphore **project backup**: import it and every job below exists,
pre-configured. Restore it from the UI (Projects → Restore from backup) or via the API:

```sh
curl -X POST https://semaphore.example.com/api/projects/restore \
  -H "Authorization: Bearer $SEMAPHORE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data @semaphore/project.json
```

Then fill in the three things a backup cannot carry:

1. **Repository URL** — `project.json` ships `https://github.com/YOUR-USER/homelab-infra.git`.
   Point it at your fork or clone.
2. **SSH key `homelab-ssh`** — the private key Ansible connects to guests with. Semaphore
   backups never contain secrets, so the key is created empty.
3. **Environment `Homelab`** — the Proxmox connection. `PROXMOX_API_HOST`,
   `PROXMOX_API_TOKEN_ID` and `PROXMOX_API_TOKEN_SECRET` are present but blank.

## Jobs

| View | Job | Playbook | Parameters |
|---|---|---|---|
| Bootstrap | Bootstrap Platform | `playbooks/bootstrap.yml` | none |
| Apps | Deploy Vaultwarden | `playbooks/apps/vaultwarden.yml` | none — `instance=vaultwarden` is baked in |
| Apps | Deploy Ntfy | `playbooks/apps/ntfy.yml` | none |
| Apps | Deploy Caddy | `playbooks/apps/caddy.yml` | none |
| Apps | Deploy Authentik | `playbooks/apps/authentik.yml` | none |
| Apps | Deploy Uptime Kuma | `playbooks/apps/uptime-kuma.yml` | none |
| Apps | Deploy Observability | `playbooks/apps/observability.yml` | none |
| Apps | Deploy PBS | `playbooks/apps/pbs.yml` | none |
| Apps | Remove App | `playbooks/apps/remove.yml` | `instance`, `app` (optional), `delete_data` |
| Maintenance | Lab Status | `playbooks/maintenance/status.yml` | none |
| Maintenance | Check Native App Updates | `playbooks/maintenance/check-native-updates.yml` | none — cron `0 6 * * 1` |
| Maintenance | Restart App | `playbooks/maintenance/restart-app.yml` | `instance` |
| Maintenance | Tail App Log | `playbooks/maintenance/tail-applog.yml` | `instance`, `lines` |
| Maintenance | Rollback Container | `playbooks/stacks/rollback-container.yml` | `stack`, `container`, `image_tag` (optional) |

**One template per app, no typing.** Each Deploy job hard-codes `-e instance=<app>`, so
deploying is one click with nothing to fill in. Running a *second* instance of an app
(`radarr-4k` alongside `radarr`) means copying its template and changing that one argument
— the same shape a new app's template takes.

**Wire Stack is deliberately absent.** `playbooks/stacks/wire-<stack>.yml` does not exist
yet (meta slice 504); app-to-app wiring has no apps to wire until a media stack app ships.
Add the template alongside the playbook.

## Environment

The `Homelab` environment carries:

| Variable | Purpose |
|---|---|
| `ANSIBLE_ROLES_PATH=ansible/roles` | Semaphore runs from the repo root; `ansible/ansible.cfg` sets `roles_path` relative to `ansible/`, so roles are only found with this set |
| `ANSIBLE_HOST_KEY_CHECKING=False` | fresh guests have unknown host keys |
| `PROXMOX_API_HOST` / `_PORT` / `_USER` / `_TOKEN_ID` / `_TOKEN_SECRET` | read by the dynamic inventory |

The `community.proxmox` dynamic inventory plugin cannot receive its connection details via `-e`
extra vars (see `ansible/inventory/proxmox.yml`) — it reads them from the process environment,
which is why they live here rather than in a vars file. `VAULTWARDEN_ADMIN_TOKEN` belongs here
too once bootstrap step 1 has issued it.

Everything else the playbooks need comes from the gitignored `config/` directory in the
repository checkout Semaphore clones — it is not part of the project backup.
