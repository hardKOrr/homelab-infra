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

Not yet automated: creating the Rundeck project and configuring the git SCM import plugin.

## Jobs

`jobs/*.yaml` is the importable job set — one file per job, each a single script step that
runs one playbook. Load them all into a project named `homelab-infra`:

```sh
for f in rundeck/jobs/*.yaml; do
  rd jobs load --project homelab-infra --format yaml --file "$f"
done
```

Job UUIDs are stable, so re-loading updates the existing jobs instead of duplicating them.

| Group | Job | Playbook | Options |
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
| Maintenance | Check Native App Updates | `playbooks/maintenance/check-native-updates.yml` | none — cron `0 0 6 ? * MON *` |
| Maintenance | Restart App | `playbooks/maintenance/restart-app.yml` | `instance` |
| Maintenance | Tail App Log | `playbooks/maintenance/tail-applog.yml` | `instance`, `lines` |
| Maintenance | Rollback Container | `playbooks/stacks/rollback-container.yml` | `stack`, `container`, `image_tag` (optional) |

**One job per app, no typing.** Each Deploy job hard-codes `instance=<app>`, so deploying is
one click. A second instance of an app means copying its job file, changing that argument and
the UUID.

**Wire Stack is deliberately absent.** `playbooks/stacks/wire-<stack>.yml` does not exist yet
(meta slice 504) — add the job alongside the playbook.

Each job step assumes the paths `bootstrap-rundeck.sh` creates: the repo at
`/var/lib/rundeck/homelab-infra` and ansible-core at `/opt/homelab-ansible/bin`. Both are
`REPO`/`VENV` variables at the top of every script step — change them there if you installed
elsewhere. Job options reach the script as `RD_OPTION_*` environment variables rather than
`${option.x}` tokens, which would collide with shell expansion.

## Credentials

The Proxmox connection comes from the gitignored `config/proxmox.yml` in the checkout, the
same file the playbooks read. Every job step wraps its `ansible-playbook` invocation in
`ansible/scripts/with-proxmox-env.sh`, which exports `PROXMOX_API_HOST` / `_PORT` / `_USER` /
`_TOKEN_ID` / `_TOKEN_SECRET` from that file — the `community.proxmox` dynamic inventory plugin
cannot receive them via `-e` extra vars (see `ansible/inventory/proxmox.yml`). Fill in
`config/proxmox.yml` and `config/infrastructure.yml` on the Rundeck host and no Key Storage
entry is needed to run any job.

Rundeck's own Key Storage still holds:

- `keys/rundeck/homelab-ssh` — the private key Ansible connects to guests with
- `keys/vaultwarden/admin-token` — optional; only if you prefer passing it as an env var
  instead of writing it to `config/infrastructure.yml` after bootstrap step 1
