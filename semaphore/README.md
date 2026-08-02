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

Mutating Semaphore templates must be **Shell/Bash** templates that execute
`ansible/scripts/semaphore-run.sh` with the ansible-relative playbook as the first argument,
for example `playbooks/apps/caddy.yml -e instance=caddy`. Repository refresh is disabled
because Semaphore already checks out the requested revision, but this still uses the same
Seed/Vault guard, Bitwarden CLI preflight, runtime mapping, and cleanup as Rundeck. Direct
Ansible templates bypass that preflight and are not a supported Vault-mode execution path.

`project.json` is the legacy Ansible-template backup and remains useful for its views,
surveys and read-only Config/Status templates. Convert each mutating template to Shell/Bash
with the wrapper above after import; backup schema varies by Semaphore release, so this repo
does not pretend an unverified `app` value is portable. The initial one-command adoption
path is Rundeck.

## Environment

The `Homelab` variable group carries non-secret values below. Add the three `BW_*` values
and the admin token as Semaphore **secret variables**, never in `project.json`'s exported
plain environment JSON.

| Variable | Purpose |
|---|---|
| `ANSIBLE_ROLES_PATH=ansible/roles` | Semaphore runs from the repo root; `ansible/ansible.cfg` sets `roles_path` relative to `ansible/`, so roles are only found with this set |
| `ANSIBLE_HOST_KEY_CHECKING=False` | fresh guests have unknown host keys |
| `BW_SERVER` | public HTTPS Vaultwarden URL |
| `BW_CLIENTID` / `BW_CLIENTSECRET` / `BW_PASSWORD` | dedicated automation account login/unlock; all three are secret variables |
| `VAULTWARDEN_ADMIN_TOKEN` | external server-control secret used only by enrollment/cutover/recovery |
| `LAB_STATE_DIR` | durable service-user-writable marker directory outside ephemeral checkouts |

After cutover, the wrapper resolves the Proxmox token and runner SSH identity from
Vaultwarden before the dynamic inventory or playbook starts. Temporary Proxmox variables
are Seed/recovery inputs only, not permanent Semaphore environment values. Install the
official `bw` CLI on every Semaphore runner and separately back up Semaphore's own access
key encryption key; losing it makes its encrypted variables unrecoverable.

The Proxmox user itself should be a dedicated `homelab-infra@pve` with a scoped role rather
than `root@pam`. `rundeck/bootstrap-rundeck.sh` creates that user and role with `pveum`; on
this path, create it the same way — the privilege list and the reasoning are in
[`rundeck/README.md`](../rundeck/README.md).
