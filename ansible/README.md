# Ansible implementation

This directory contains the provisioning and operating implementation. It must remain
independent of Rundeck and other operator interfaces. Operator interfaces select and run
playbooks; they do not define Ansible behavior.

## How a run executes

A Rundeck job calls `lab-run`, the installed entry point for
`ansible/scripts/lab-run.sh`. The wrapper reads its runner environment from
`/etc/homelab-infra/lab-run.env` (or `LAB_ENV_FILE`). With `LAB_REFRESH=1`, it
refreshes the checkout to `origin/$LAB_BRANCH` and re-executes the refreshed wrapper;
refresh defaults off for every caller, including a bootstrapped runner. `LAB_DOCTOR`
defaults to `1`, validating configuration before the selected playbook runs; set it to
`0` to disable that check. The playbook orchestrates reusable roles and task files.

Before cutover, explicitly allowed Seed-mode runs (`LAB_SEED_MODE=1`) consume the
bootstrap secrets. After the vault-mode marker exists, ordinary runs must unlock
Vaultwarden, and Seed mode cannot bypass that guard. The explicit vault recovery
playbook has its own guarded recovery path.

Platform defaults merge recursively with `config/proxmox.yml` and
`config/infrastructure.yml`; application defaults merge with `config/apps/<instance>.yml`.
Generated topology and the in-memory secret overlay complete the runtime view. See
[`vars/CONTRACT.md`](vars/CONTRACT.md) for the namespaces and exact precedence.

Proxmox guests are owned by the `_+lab` tag. Provisioning and maintenance must preserve
that ownership boundary and leave untagged guests alone.

## Running directly

`lab-run` is the entry point for every caller; Rundeck is one of them. A direct run uses
the runner's network position, `config/` and lab state, so it runs on the runner as the
`rundeck` user, the account that owns `config/` and runs every job step.

Enable direct runs once after cutover: run **Setup / Credentials / Vaultwarden Login
File** with your Ansible Vault password. After the job has unlocked Vaultwarden with the
automation login from Key Storage, it stores that login in `config/vaultwarden.yml`, each
value encrypted with the password. Run it again after rotating the automation login or
changing the password.

A direct run then supplies only the Ansible Vault password, through `LAB_VAULT_PASSWORD`,
`ANSIBLE_VAULT_PASSWORD_FILE` (a file, or an executable that prints it),
`ANSIBLE_VAULT_IDENTITY_LIST=lab@prompt`, or the prompt `lab-run` shows on a terminal.
`lab-run` reads it once, unsets those variables, fills the missing `BW_CLIENTID`,
`BW_CLIENTSECRET` and `BW_PASSWORD` from the file, and continues through the same
Vault-mode guard and Vaultwarden preflight as a job. A file it cannot decrypt stops the
run. The Vaultwarden Login File playbook (`playbooks/maintenance/vaultwarden-login-file.yml`)
rewrites that file, so a direct run of it gets the `LAB_VAULT_PASSWORD` value back after
the preflight.

`LAB_LOG_BRIEF=1` shortens a direct run's log for a reader that pays per line, such as an
LLM agent: no colour codes, no routine `lab-run` lines, no repeated TLS warning, the config
check only when it fails, and a TASK banner only for a task that changed something,
failed or reported a finding. Rundeck jobs never set it, so operators keep every banner.

The file also holds an edge copy: the platform items of the Vaultwarden contract, without
any application's credentials. Caddy serves `BW_SERVER`, so deploying Caddy
(`playbooks/apps/caddy.yml`) or removing it (`playbooks/apps/remove.yml` with app
`caddy`) cannot depend on reaching Vaultwarden. For those runs only, when the login fails
and Vaultwarden does not answer, `lab-run` takes the contract from the edge copy instead
and the run continues. Each such run given `LAB_VAULT_PASSWORD`, and each Vaultwarden Login
File run, rewrites the copy from a verified Vaultwarden session, so it follows rotations.

Run a candidate revision from its own checkout beside the runner checkout, with refresh off
and `config/` linked to the runner's. On the runner (`pct exec <runner-vmid>` from its
node), as root:

```sh
c=/var/lib/rundeck/homelab-infra-candidate
sudo -u rundeck -H bash -c '
  set -e
  [ -d "$1/.git" ] || git clone --quiet "$(git -C /var/lib/rundeck/homelab-infra remote get-url origin)" "$1"
  [ -e "$1/config" ] || ln -s /var/lib/rundeck/homelab-infra/config "$1/config"
  git -C "$1" fetch --quiet origin "$2"
  git -C "$1" checkout --quiet --detach FETCH_HEAD
  git -C "$1" log --oneline -1' _ "$c" <branch>

IFS= read -r LAB_VAULT_PASSWORD; export LAB_VAULT_PASSWORD
sudo -u rundeck -H --preserve-env=LAB_VAULT_PASSWORD env LAB_REPO="$c" LAB_REFRESH=0 \
  bash "$c/ansible/scripts/lab-run.sh" playbooks/apps/<app>.yml -e instance=<instance>
```

Feed the password on root's stdin from wherever you keep it, for example through
`ssh root@<pve-node> pct exec <runner-vmid> -- ...`. Pass it in the environment as above:
the `rundeck` user cannot reopen root's stdin as `/dev/stdin`, and a command argument
would show it to every local account. `/etc/homelab-infra/lab-run.env`
still supplies the venv, `BW_SERVER` and CA settings, and `LAB_STATE_DIR` defaults to
`/etc/homelab-infra/state`, so the run sees the same lab mode as every job. The runner
checkout stays on `LAB_BRANCH` for Rundeck.

To check Kometa's Plex endpoint from the cluster, run its deployed CronJob once, and
require a clean completion log, use `lab-run playbooks/maintenance/run-kometa.yml`.

## Shared storage availability

Run `lab-run playbooks/maintenance/check-shared-storage.yml` to check every cluster
node against the storage definitions marked `shared` by Proxmox, including NFS and
ZFS over iSCSI. It reads `/storage`, `/nodes` and `/nodes/<node>/storage` with
`pvesh get`; it changes no node or guest and is independent of Deploy. Each node lists
every shared storage as `active` or `missing`. Inactive or absent entries count as
missing even when a storage's node allowlist excludes that node. It prints all node
reports before exiting non-zero for missing storage or a failed node status query.
No shared definitions produces an explicit empty report and succeeds.

## Docker stack hosts

Docker apps select a shared host with `stack`. Host type and sizing come from
`vars/stack-defaults.yml`, overridden by `stacks.<stack>` in `config/infrastructure.yml`.
`guest_type` defaults to `vm`; `lxc` creates an unprivileged container through the same
Debian template and provisioning tasks as native LXCs, with `nesting=1,keyctl=1`.
Both types use the Docker role to install and configure Docker.

For example, set `stack: services-lxc` in `config/apps/actual-budget.yml` to select the
provided LXC stack. Every tenant must declare `docker-lxc` in `hosting.supported`.
Applications requiring a privileged container declare `docker-lxc` under
`hosting.impossible` with the technical reason; privileged stack LXCs are not supported.
PCI/device passthrough stacks require a VM. Changing `guest_type` never converts an
existing host: select a new stack name and move the app using its backup/restore path.
Sizing and placement apply at creation; other tenants stay on their existing stacks.

## Live operations

Live runs may overlap; there is no lab lock. Ordinary runs use the on-disk checkout, so a
push does not change files under a running job. `LAB_REFRESH=1` is an explicit shared
checkout update; the Reimport Jobs path also refreshes before importing job definitions.
The [recovery drill and application data port](playbooks/maintenance/recovery-drill.md)
are the live procedures for one instance; moving PBS with its datastore is in
[`roles/pbs/README.md`](roles/pbs/README.md#moving-pbs-to-another-node-with-its-datastore).

### Fixing and testing a change

1. **Fix loop.** Change the owning code, run the gates, push the branch, update the
   candidate checkout and run the affected playbooks directly. Read the failure, fix and
   rerun until the checks pass. Run the complete
   [application recovery drill](playbooks/maintenance/recovery-drill.md) from the final
   candidate revision before merge. Bootstrap runs only for changes to bootstrap or the
   runner itself.
2. **Job definitions.** A change to `rundeck/jobs/`, `rundeck/render-job.py` or how job
   options reach `lab-run` is also checked through Rundeck at the same revision: point the
   runner at the branch as [`../rundeck/README.md`](../rundeck/README.md) describes,
   Reimport Jobs, run the changed job, and restore the usual branch after merge.
3. **PR and merge.** Put the gate results, the tested SHA, the direct Ansible results
   and the execution IDs of any Rundeck checks in the PR. Retest after any change to the
   candidate. The issue can close at merge.

## Areas

- `playbooks/` contains orchestration entry points for deployment, removal, maintenance,
  provisioning, stacks, and bootstrap ([README](playbooks/README.md)).
- `tasks/` contains reusable flows and integration seams shared by playbooks and roles
  ([README](tasks/README.md)).
- `roles/` configures applications and platform components, one role per catalog app
  (`roles/<app>/`; the exceptions are `role_aliases` in
  [`gate/structure.yml`](../gate/structure.yml)). The `_template-*` roles are the starting
  points for new applications. Roles with a README: [authentik](roles/authentik/README.md),
  [bazarr](roles/bazarr/README.md), [caddy](roles/caddy/README.md),
  [comfyui](roles/comfyui/README.md), [deemix](roles/deemix/README.md),
  [forgejo](roles/forgejo/README.md), [frigate](roles/frigate/README.md),
  [home-assistant](roles/home-assistant/README.md), [mariadb](roles/mariadb/README.md),
  [navidrome](roles/navidrome/README.md), [observability](roles/observability/README.md),
  [ollama](roles/ollama/README.md), [pbs](roles/pbs/README.md), [pdm](roles/pdm/README.md),
  [plex](roles/plex/README.md), [postgresql](roles/postgresql/README.md),
  [qbittorrent](roles/qbittorrent/README.md), [sabnzbd](roles/sabnzbd/README.md),
  [servarr](roles/servarr/README.md).
- `vars/` contains Git-managed defaults (`vars/app-defaults/<app>.yml`) and the
  authoritative configuration [contract](vars/CONTRACT.md), split by config file under
  `vars/contract/`.
- `inventory/` and `inventory_plugins/` resolve the managed Proxmox estate into Ansible
  hosts and groups.
- `scripts/` contains runner entry points and committed helpers used by jobs and tasks.
- `callback_plugins/` defines the common job and terminal output; `library/` holds
  controller-side modules.
- `files/` contains controller-side helper programs installed or called by tasks, grouped
  as `files/decommission/`, `files/pbs/`, `files/proxmox/` and `files/recovery/`.
- `templates/kubernetes/` holds the shared Kubernetes recovery CronJob and Job.

## Shared operations

A role uses the shared file for any operation listed here and supplies its per-app
behavior through that file's inputs or hook. When the shared file lacks a needed seam, add
the seam there. [`gate/test-shared-standards.py`](../gate/test-shared-standards.py)
fails on a copy of a recovery primitive outside its shared file; every role already uses
the shared files, so its allowlists are empty.

| Operation | Owner | A role supplies |
| --- | --- | --- |
| Proxy, DNS, SSO and monitoring wiring | `tasks/wiring/<provider>.yml`, inverse `tasks/unwiring/` ([README](tasks/wiring/README.md)) | `wiring_app_name`, `wiring_upstream_host`/`_port`/`_scheme`, `wiring_domain`, `wiring_access`, `wiring_identity_mode` |
| Firewall access | `tasks/firewall/open-app-access.yml` | `wiring_app_name`, `wiring_upstream_host`, `wiring_firewall`, optional `firewall_sends_mail`, `firewall_inbound_ports` |
| Kubernetes workload apply | `tasks/kubernetes/apply-manifest.yml` ([README](tasks/kubernetes/README.md)) | `k8s_manifest`, `k8s_namespace`, `k8s_api_host`, optional `k8s_wait_for` |
| Kubernetes external media NFS volumes | `tasks/kubernetes/prepare-media-volumes.yml` ([README](tasks/kubernetes/README.md)) | `k8s_media_shares` entries (`name`, `server`, `path`, `mount_path`, boolean `read_only`); returns `k8s_media_volumes` and `k8s_media_volume_mounts` for workload templates |
| Kubernetes NodePort claim check | `tasks/kubernetes/claim-node-port.yml` | `k8s_node_port`, `k8s_namespace`, `k8s_api_host`; run before every apply of a NodePort Service |
| Kubernetes one-shot Job | `tasks/kubernetes/run-job.yml` | `k8s_job_name`, `k8s_namespace` and the rendered Job; returns `k8s_job_succeeded`, `k8s_job_exit_code` and logs, with optional `k8s_job_expected_exit_codes` |
| Restore validation | `tasks/recovery/validate-restore.yml` | `recovery_*` plan inputs and the provider's evidence |
| Restore swap: stage, stop, move aside, install with owner, start, roll back | `tasks/recovery/restore-swap.yml`, running `scripts/restore-swap.sh` | `recovery_swap_runtime`, `recovery_swap_archives`, `recovery_swap_paths` (`path`, `source`, numeric, named or `live` `owner`/`group`, `mode`, `mount`, `allow_empty`), `recovery_swap_stop_services` with `recovery_swap_compose_project`, optional `recovery_swap_pbs_runtime`, `recovery_swap_staging_parent`, `recovery_swap_database` (`config`, `dump`), `recovery_swap_health_url` with its `_status`, `_headers` and `_follow_redirects`, and the `staged`, `stopped`, `replaced`, `verified` and `rollback` hooks |
| A recent pre-restore point of the target, before a swap | `tasks/recovery/require-recent-pre-restore-point.yml` | `recovery_pre_restore_backup_id`, optional `recovery_pre_restore_max_age` (seconds, default one hour) |
| PBS target and credentials | `tasks/backup/resolve-pbs-target.yml` | optional `k8s_pbs_datastore_override` |
| Application database provisioning | `tasks/database/provision.yml` | `database_provision_app_instance`, `database_provision_config` |
| PBS client install, backup, verify, restore | `tasks/backup/pbs-client-install.yml`, `pbs-client-backup.yml`, `pbs-client-restore.yml`, all running `scripts/pbs-client.sh`; pins in `vars/pbs-client.yml` | `pbs_client_runtime` (`docker` or `native`), `pbs_client_archives`, optional `pbs_client_stop_services` with `pbs_client_compose_project`, `pbs_client_backup_args`, `pbs_client_backup_mounts` (more host directories inside one archive), `pbs_client_keep_last`, `pbs_client_prune`, `pbs_client_restore_args`, `pbs_client_quiesced_hook` |
| PBS client on the guest, for a recovery script that runs without Ansible (cron) | `tasks/backup/pbs-client-script.yml`, installing `scripts/pbs-client.sh` and the instance's root-only client settings | `pbs_client_runtime`, `pbs_client_config_state` (`present` or `absent`); the script then runs `backup`, `list`, `verify` or `extract` with `PBS_CLIENT_CONFIG` set |
| Kubernetes backup CronJob and restore Job | `tasks/kubernetes/recovery-backup.yml` and `recovery-restore.yml`, rendering `templates/kubernetes/recovery-*.yaml.j2` | `k8s_recovery`: `archives` (PVC `claim` with optional `owner`, or `config_map`); a PVC may set `dump_writable: true` to give the database dump init container a writable `/volumes/<name>` mount (requires a database step and no `config_map`; the PBS push mount stays read-only); optional `database` step (restore may set `archive` and `archive_path` to read a nested dump from an application archive; defaults to `database.pxar`), backup `stop` list and positive `stop_replicas` fallback, restore `stop` and `restart` Deployments, optional `after_restore_tasks` hook. When a backup has both `database` and `stop`, its database image needs a package manager that can install Python, and its client image needs Python so failure traps can resume the app after the database dump and volume archive. |
| Kubernetes database dump, load and inspect step | `tasks/database/kubernetes-recovery-step.yml` | `k8s_database_step_source` (`registered` or `namespace-mysql`), the instance and its config |
| Database dump, restore and drop | `tasks/database/dump.yml`, `restore.yml`, `drop.yml`, running `scripts/database-client.sh`; Remove drops any registered-backend database | `database_app_instance`, `database_app_config` (`app.database`), `database_dump_file` or `database_restore_file`; extensions that need a superuser and privileges stay `provision.yml` inputs; a restore recreates the extensions the application's login created |
| Whole MariaDB/MySQL backend guest export and restore | `tasks/database/install-server-recovery.yml`, installing `scripts/server-recovery.py` | the engine (`mariadb` or `mysql`) as its first argument; MariaDB's restore takes `--data-dir` and `--service` |

The guest-side PBS client runs with `/tmp`, its cache and runtime files on a bounded tmpfs,
in a short-lived container on a Docker host or in a private mount namespace on a native
guest. Backup names each recovery point by its UTC time and keeps the previous point
through a same-day safety backup. A Restore preview (`overwrite=false`) runs the same
client path: it checks the point's archives and downloads its index without touching the
application. A role that stops its application before extracting runs that preview on
every restore first (`pbs_client_overwrite: false`), so a point from another group, a
missing PBS, or a point without the archives fails while the application still serves.

A Docker or native restore replaces live state through `tasks/recovery/restore-swap.yml`.
It extracts and checks the point while the application still serves, stops it, moves each
live directory aside to `.<name>.restore-previous` beside it (a rename on the same
filesystem), installs the restored directory, gives every file the declared numeric owner
and never the archive's, and starts the application. Any failure after the stop puts the
originals back and starts them. A rollback copy left by an earlier failed run refuses the
next restore until someone puts it back or removes it. A path that is a mount point, such
as media on root-squashed NFS, swaps its contents inside `<path>/.restore-previous`, and
each step there runs as the owner. The mount keeps its own mode unless the role declares
one. A role whose point holds a database dump passes `recovery_swap_database`: the swap
verifies the dump and that no other application claims the database before the stop,
dumps the live database after it, loads the archived
dump once the paths are installed, and loads the live dump back on rollback.
Stage backup sources and restore targets outside `/tmp`, beside the data they replace.

## Local conventions

- Keep playbooks focused on orchestration. Put reusable behavior in a task file or role.
- Use `no_log` only on tasks that carry secrets; failures print stderr and stdout with secret values scrubbed.
- Preserve sibling keys when updating shared `homelabinfra_*` mappings. Follow
  `vars/CONTRACT.md` for merge and namespace rules.
- Treat `config/.generated/facts.yml` as generated topology, not a secret store.
- Keep application deployment and day-2 playbooks safe to run again.
