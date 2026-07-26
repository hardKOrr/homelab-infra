# Semaphore Job Definitions

Both UIs are supported and the playbooks are UI-agnostic. **The Rundeck path is the more
finished one**: `rundeck/bootstrap-rundeck.sh` builds the runner, mints the Proxmox
credential, authors `config/`, imports the jobs and stages Key Storage in one command.
There is no `bootstrap-semaphore.sh` — on this path you build the runner yourself, and the
steps below are what that costs. Layer 2 (the **Bootstrap Platform** job) is identical
either way.

## Import

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
   backups never contain secrets, so the key is created empty. Its public half must be the
   `ansible.ssh_public_key` in `config/proxmox.yml`, because that is what gets deployed to
   every guest.
3. **Environment `Homelab`** — the Proxmox connection and the two platform secrets. See
   below.

## Where config lives on this path

Semaphore checks the repository out per project on its own host. `config/` is gitignored,
so it is **not** in that checkout and cannot arrive through git. Place it in Semaphore's
repository directory (typically `/home/semaphore/repositories/<project>/config/`), where it
persists across runs.

The **Config** job group then works from there exactly as it does under Rundeck:

- **Config Doctor** validates everything and names each problem by file and key path.
- **Configure App** writes `config/apps/<instance>.yml` from a survey; blank fields keep
  their current value, the previous content is kept under `config/apps/.backups/`, and the
  task log shows a diff.
- **Get Config** reads the set back out, redacted, and writes an unredacted `tar.gz`
  restore point under `artifacts/` on the Semaphore host.

So the one-time placement of `config/proxmox.yml` and `config/infrastructure.yml` is manual
here, and everything after it is not.

## Jobs

| View | Job | Playbook | Parameters |
|---|---|---|---|
| Bootstrap | Bootstrap Platform | `playbooks/bootstrap.yml` | none |
| Config | Config Doctor | `playbooks/maintenance/config-doctor.yml` | none |
| Config | Configure App | `playbooks/maintenance/configure-app.yml` | `instance` + a dozen optional overrides + `extra_yaml` |
| Config | Get Config | `playbooks/maintenance/get-config.yml` | `instance` (optional), `archive` (optional) |
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
| Maintenance | Wire Media Stack | `playbooks/stacks/wire-media-stack.yml` | none |

**One template per app, no typing.** Each Deploy job hard-codes `-e instance=<app>`, so
deploying is one click with nothing to fill in. Running a *second* instance of an app
(`radarr-4k` alongside `radarr`) means copying its template and changing that one argument
— the same shape a new app's template takes.

There is no `Reimport Jobs` template: Semaphore keeps its templates in its own database,
and they change when you re-import `project.json`.

Semaphore also needs no `lab-run.sh`. It clones the repository itself before every task, so
the checkout is current by construction — the problem `lab-run.sh` solves for Rundeck does
not exist here, and Semaphore's templates name the playbook directly.

## Environment

The `Homelab` environment carries:

| Variable | Purpose |
|---|---|
| `ANSIBLE_ROLES_PATH=ansible/roles` | Semaphore runs from the repo root; `ansible/ansible.cfg` sets `roles_path` relative to `ansible/`, so roles are only found with this set |
| `ANSIBLE_HOST_KEY_CHECKING=False` | fresh guests have unknown host keys |
| `PROXMOX_API_HOST` / `_PORT` / `_USER` / `_TOKEN_ID` / `_TOKEN_SECRET` | read by the dynamic inventory |
| `PROXMOX_API_TOKEN` | the same secret as `_TOKEN_SECRET`, read by `load-user-vars.yml` and `with-proxmox-env.sh` |
| `VAULTWARDEN_ADMIN_TOKEN` | fill in once bootstrap step 1 has produced it |

`PROXMOX_API_TOKEN_SECRET` and `PROXMOX_API_TOKEN` hold the **same value** and exist
separately only because they have different readers: the dynamic inventory plugin cannot
receive its connection details via `-e` extra vars (see `ansible/inventory/proxmox.yml`)
and reads `_TOKEN_SECRET` from the process environment, while the playbooks and the shell
wrapper read `PROXMOX_API_TOKEN`. Set both.

**This environment is where the Proxmox secret belongs — not in `config/proxmox.yml`.**
The config file carries the connection's *shape* so it can be read, diffed, reviewed and
handed around by the Get Config job; the secret stays in exactly one place. Leaving
`api_token_secret` out of the file entirely is the recommended shape, and the environment
wins over the file when both are set. See `config.example/proxmox.yml` and
`ansible/vars/CONTRACT.md` §5.

The Proxmox user itself should be a dedicated `homelab-infra@pve` with a scoped role rather
than `root@pam`. `rundeck/bootstrap-rundeck.sh` creates that user and role with `pveum`; on
this path, create it the same way — the privilege list and the reasoning are in
[`rundeck/README.md`](../rundeck/README.md).
