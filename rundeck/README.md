# Rundeck

## Bootstrap

`bootstrap-rundeck.sh` stands the whole UI layer up from nothing. Copy it to a Proxmox node and
run it as root:

```sh
scp rundeck/bootstrap-rundeck.sh root@<node>:/root/
ssh root@<node> 'bash /root/bootstrap-rundeck.sh'
```

It creates an unprivileged Debian 13 LXC and installs OpenJDK 21, Rundeck 6, and ansible-core
2.18 in a venv at `/opt/homelab-ansible`, with the collections pinned in
`ansible/requirements.yml` installed to the `rundeck` user's default collections path. The repo
is cloned to `/var/lib/rundeck/homelab-infra`.

Everything is idempotent — re-running converges an existing container. It will not rotate an
admin password or reissue an API token you already have.

Override any of the defaults with environment variables (`VMID`, `CT_IP`, `CT_GW`, `CT_STORAGE`,
`TEMPLATE`, `REPO_URL`, …); see the header of the script.

Credentials land in `/root/.rundeck-bootstrap` (0600) inside the container. Copy
`RUNDECK_API_TOKEN` from there into the repo's gitignored `.env`.

**Debian 13 is not incidental.** `community.proxmox` 2.0.0 requires ansible-core >= 2.17, which
requires a Python 3.11+ controller. Debian 11 (Python 3.9) cannot run this codebase at all.

Not yet automated, pending apps and wiring: creating the Rundeck project, configuring the git
SCM import plugin, staging Key Storage entries, and the `jobs/*.yaml` below.

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

The `community.proxmox` dynamic inventory plugin cannot receive its connection details via `-e`
extra vars (see `ansible/inventory/proxmox.yml`). The Ansible job step must export
`PROXMOX_API_HOST` / `PROXMOX_API_PORT` / `PROXMOX_API_USER` / `PROXMOX_API_TOKEN_ID` /
`PROXMOX_API_TOKEN_SECRET` (sourced from `keys/proxmox/api-token`) before invoking `ansible`, or
wrap the invocation in `ansible/scripts/with-proxmox-env.sh <user-vars.yml> <ansible-command>...`.

## TODO: jobs/*.yaml
Rundeck job definition YAML files — one per job, importable via Rundeck API or UI.
