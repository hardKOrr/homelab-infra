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

## Live operations

Live runs may overlap; there is no lab lock. Ordinary runs use the on-disk checkout, so a
push does not change files under a running job. `LAB_REFRESH=1` is an explicit shared
checkout update; the Reimport Jobs path also refreshes before importing job definitions.

### Fixing and testing a change

1. **Fix loop.** Change the owning code, run the gates, push the branch, update the
   candidate checkout and run the affected playbooks directly. Read the failure, fix and
   rerun until the checks pass. Run the complete application drill from the final
   candidate revision before merge. Bootstrap runs only for changes to bootstrap or the
   runner itself.
2. **Job definitions.** A change to `rundeck/jobs/`, `rundeck/render-job.py` or how job
   options reach `lab-run` is also checked through Rundeck at the same revision: point the
   runner at the branch as [`../rundeck/README.md`](../rundeck/README.md) describes,
   Reimport Jobs, run the changed job, and restore the usual branch after merge.
3. **PR and merge.** Put the gate results, the tested SHA, the direct Ansible results
   and the execution IDs of any Rundeck checks in the PR. Retest after any change to the
   candidate. The issue can close at merge.

### Application recovery drill

The drill proves deploy, convergence and native recovery for one instance. Running it
authorizes every step below on the issue's target instance, including Remove with
`delete_data=true` and overwrite Restore. Go straight through. A lab-owned resource in the
way, such as another instance of the same application, a shared backend or a failed run's
leftovers, is yours to remove, redeploy or reconfigure; redeploy any instance you removed
once the drill ends. Stop only for a guest without the `_+lab` tag, a safety guard that
trips, or a failure the issue's scope cannot fix. Read
inputs from the playbook headers and the application's documentation; Rundeck options map
to the same inputs.

1. Verify the target carries the lab tag, its dependencies exist (deploy them if absent),
   and the candidate checkout is at the intended revision. Choose a few checks that show
   other applications and the platform are unaffected.
2. Deploy. Verify HTTPS and login with the canonical credential, and that the declared
   DNS, ingress, identity provider and monitor wiring each appear exactly once.
3. Create the issue's data and configuration canaries through the application's normal
   interface and read both back. Deploy again; both survive and the wiring stays unique.
4. Back up through the application's backup playbook. Record the recovery point and
   verify it exists.
5. Remove with `delete_data=true`. For a drill that requires a fresh native guest, add
   `destroy_guest=true`; this is limited to one `_+lab` LXC or VM carrying the exact
   instance tag and rechecks its live tags before destruction. Verify the instance, its
   data and wiring are gone, the backup remains, and other applications are unaffected.
   Without that explicit option, Remove retains the guest for redeployment.
6. Deploy a fresh instance with the same name. Verify it works and neither canary is
   present. An overwrite Restore of an existing target requires `pre_restore_point`;
   back up the fresh instance with Backup App, verify that point exists, and pass it as
   `pre_restore_point`. Run Restore with `overwrite=false` to preview, then restore the
   recovery point recorded in step 4 with overwrite.
7. Read both canaries back exactly, verify login and unique wiring, then Deploy once more.
   Restored data, configuration and the other applications remain.

After the fresh Deploy in step 6, an SSO-protected route can return 404 for up to about
five minutes while the Authentik embedded outpost reconnects. Wait and retry the same
check in steps 6/7; restart nothing.

A check that cannot be read back counts as unverified.

For a stateless application, skip backup and restore and prove a fresh deploy recreates
its configuration and wiring. For multiple instances, verify siblings stay intact. If the
application has no backup and restore yet, build them in the repository before step 4.
For those new implementations, `restore.yml` must move the original state aside on the
same filesystem before replacing it and put it back on any later failure, as
[`roles/tautulli/tasks/restore.yml`](roles/tautulli/tasks/restore.yml) and
[`roles/plex/tasks/restore.yml`](roles/plex/tasks/restore.yml) do. Add gate regressions for
failures after the move.
Restoring a whole stack guest does not prove one application's restore.

MariaDB native recovery runs `mariadb-dump` over the guest's local root socket with a global
read lock, capturing all persistent databases, routines, events, triggers and grant tables
at one consistent point. PBS stores the logical export and a version/checksum manifest as
`mariadb.pxar` in `host/<instance>`. Restore verifies the export and compatible major/minor
version before stopping MariaDB, moves the original data directory aside on the same
filesystem, initializes a clean system database, and imports the selected point. It puts
the original directory back if initialization, import or health verification fails.

After the drill, when no PR is needed, post the result comment and close the issue.

PostgreSQL native recovery uses `pg_basebackup` over the guest's local socket as the
`postgres` superuser. PBS stores the full PostgreSQL 16 cluster, including databases,
roles and grants, as `postgresql.pxar`; the backup manifest is verified before upload and
restore. Restore validates the target major version, swaps the cluster data directory
while its service is stopped, then checks readiness and local-socket superuser access.
If verification fails, the original data directory is put back. Recovery currently
requires the standard `pg_default` and `pg_global` tablespaces.

Deemix native recovery stops only the selected service and captures all of
`config_path` (settings and the optional ARL) as one PBS `data.pxar` in
`host/<instance>`. Downloads stay outside the recovery unit. The two newest
snapshots are retained alongside daily retention so a pre-restore safety backup
keeps the prior recovery point. Restore previews until `overwrite=true`, checks
the source group, and keeps the original config on its filesystem until the
replacement passes health checks. Any later failure puts the original back and
leaves the service stopped. An empty ARL supports settings-only use; the human
route still uses its declared Authentik access path. Deploy preserves an existing
or restored `.arl` when the configured ARL is empty, and publishes that effective
value to the canonical media credential for Lidarr. A non-empty configured ARL
rotates the file and restarts Deemix.

Plex native recovery captures its stopped service's server identity, library databases,
Metadata and Media as one PBS `data.pxar` in `host/<instance>`. Cache, Codecs, Crash
Reports and Logs are excluded; external media mounts and transcode are outside the
recovery unit. Restore requires `overwrite=true`, stages on the config volume, accepts
only snapshots in the selected `host/<backup id>` group, preserves declared media paths, and
publishes the restored server token to `homelab-infra/media/<instance>` after an
authenticated API check.

Prowlarr, Sonarr, Radarr and Lidarr share Servarr native recovery. Backup stops only the
selected instance and captures its config directory (including `<app>.db`, `config.xml`
and the verified API key) as one PBS `data.pxar` in `host/<instance>`. Logs, `logs.db`
and local `Backups` are excluded; media mounts remain outside recovery. Pruning retains
the two newest snapshots in addition to daily retention, so a same-day pre-restore safety
backup keeps the immediately preceding recovery point. Restore previews
without mutation until `overwrite=true`, validates the selected backup group and staged
database/configuration, then adopts the archived API key in Compose and the canonical
`homelab-infra/media/<instance>` item after an authenticated API check. A subsequent
Deploy keeps that key and the recovered application state.
Restore requires both Compose API-key overrides before replacing state, and retains the
original config directory beside the live path until verified recovery succeeds. A failed
restore leaves the service stopped and reports the retained directory for inspection;
it does not automatically put that directory back or revert the Compose API-key overrides.

Bazarr native recovery stops only the selected service and archives its config directory
as one PBS `data.pxar` in `host/<instance>`, excluding `log` and `cache`. Backup retains
the two newest snapshots alongside daily retention, preserving the selected recovery
point when the fresh instance is backed up. Restore previews until `overwrite=true`,
checks the source group, and retains the original directory on the same filesystem until
an authenticated settings check and canonical API-key publication succeed. A failure
puts the original state back and leaves Bazarr stopped. The final Deploy keeps the
restored API key, language profiles and settings.

Observability native recovery stops only its Prometheus and Grafana Compose services and
archives the complete data directory, preserving the Prometheus TSDB and Grafana's saved
dashboards and alert rules together. Deploy regenerates the config and provisioning files.
Restore checks the selected PBS point and, for an existing target, a separate pre-restore
point; it stages the archive and keeps the original data directory on the same filesystem
until both services pass health checks. A failed replacement puts the original data back
and leaves Observability stopped.

SABnzbd Deploy registers only that client in each deployed Usenet consumer and creates
missing consumer categories. Existing categories and other client entries are preserved.
Consumer failures are recorded while the remaining connections are attempted; the play's
final degradation check fails the run with the collected problems. Wire Media Stack still
reconciles all compatible download clients and the other media connections across the stack.
Remove withdraws only its named entries after checking that they address the owned client.

SABnzbd native recovery stops only the selected service and captures its configuration
and history as one PBS `data.pxar` in `host/<instance>`. Logs and download directories
are excluded; external media mounts stay outside the archive. Backup keeps the two newest
snapshots alongside daily retention. Restore previews until `overwrite=true`, checks the
source group, and keeps the original directory beside the config path until the archived
API key passes an authenticated queue check and is published to the canonical credential
item. A failure puts the original directory back and leaves SABnzbd stopped. Deploy keeps
the recovered INI, API key, categories and application settings.

qBittorrent native recovery stops only the selected service and archives its config,
preferences and torrent resume metadata as one PBS `data.pxar` in `host/<instance>`.
Logs and downloads are excluded; payloads remain on the external media mount. Backup
keeps the two newest snapshots alongside daily retention. Restore previews until
`overwrite=true`, validates the backup group and staged configuration, and retains the
original config on the same filesystem until the canonical WebUI credential works.
Failure puts the original state back and leaves the service stopped. Deploy preserves
recovered preferences and torrents and registers only this client in deployed torrent
consumers. Remove withdraws only entries that address the owned client.

Forgejo native recovery stops only the selected service and stores a PostgreSQL dump and
the complete data directory (repositories, LFS, attachments, and `app.ini` secrets) in
one PBS `data.pxar` under `host/<instance>`. The service stays stopped while the database
and files are captured. Restore stages and validates both parts, keeps the existing data
directory beside its original path and a database dump until Forgejo passes its health
check, and restores both originals on failure. It leaves Forgejo stopped after a failed
restore and retains the two newest recovery points alongside daily retention.

### Application data port

The issue names the source, destination, capture method, required path or address
changes, and the counts to compare. Source access comes from the operator's verified
private values. The source is not tagged `_+lab`, so it is read-only: capture from it, and
never stop, change or delete it. If it has no consistent read-only export, report that as
the blocker.

Capture only application data, through a consistent export or backup, and keep the copy
private. Change only the paths, peer addresses and public URL the destination needs.
Import through the repository's Restore or Import path into a fresh instance; if that
path is missing, build it in the repository.

Verify the issue's counts and spot checks, a real account's login and unique wiring. Back
up the destination through homelab-infra, record its first recovery point, and verify a
final Deploy keeps the imported data.

Platform defaults merge recursively with `config/proxmox.yml` and
`config/infrastructure.yml`; application defaults merge with `config/apps/<instance>.yml`.
Generated topology and the in-memory secret overlay complete the runtime view. See
[`vars/CONTRACT.md`](vars/CONTRACT.md) for the namespaces and exact precedence.

Proxmox guests are owned by the `_+lab` tag. Provisioning and maintenance must preserve
that ownership boundary and leave untagged guests alone.

## Areas

- `playbooks/` contains orchestration entry points for deployment, removal, maintenance,
  provisioning, stacks, and bootstrap.
- `tasks/` contains reusable flows and integration seams shared by playbooks and roles.
- `roles/` configures applications and platform components. The `_template-*` roles are
  the starting points for new applications.
- `vars/` contains Git-managed defaults and the authoritative configuration contract.
- `inventory/` resolves the managed Proxmox estate into Ansible hosts and groups.
- `scripts/` contains runner entry points and committed helpers used by jobs and tasks.
- `callback_plugins/` defines the common job and terminal output.
- `files/` contains controller-side helper programs installed or called by tasks.

### Shared operations

A role uses the shared file for any operation listed here and supplies its per-app
behavior through that file's inputs or hook. When the shared file lacks a needed seam, add
the seam there. [`gate/test-shared-standards.py`](../gate/test-shared-standards.py)
fails on a new copy of a recovery primitive outside its shared file; its allowlist of
today's copies only shrinks.

| Operation | Owner | A role supplies |
| --- | --- | --- |
| Proxy, DNS, SSO and monitoring wiring | `tasks/wiring/<provider>.yml`, inverse `tasks/unwiring/` ([README](tasks/wiring/README.md)) | `wiring_app_name`, `wiring_upstream_host`/`_port`/`_scheme`, `wiring_domain`, `wiring_access`, `wiring_identity_mode` |
| Firewall access | `tasks/firewall/open-app-access.yml` | `wiring_app_name`, `wiring_upstream_host`, `wiring_firewall`, optional `firewall_sends_mail`, `firewall_inbound_ports` |
| Kubernetes workload apply | `tasks/kubernetes/apply-manifest.yml` ([README](tasks/kubernetes/README.md)) | `k8s_manifest`, `k8s_namespace`, `k8s_api_host`, optional `k8s_wait_for` |
| Kubernetes one-shot Job | `tasks/kubernetes/run-job.yml` | `k8s_job_name`, `k8s_namespace` and the rendered Job |
| Restore validation | `tasks/recovery/validate-restore.yml` | `recovery_*` plan inputs and the provider's evidence |
| PBS target and credentials | `tasks/backup/resolve-pbs-target.yml` | optional `k8s_pbs_datastore_override` |
| Application database provisioning | `tasks/database/provision.yml` | `database_provision_app_instance`, `database_provision_config` |
| PBS client install, backup, verify, restore | `tasks/backup/pbs-client-install.yml`, `pbs-client-backup.yml`, `pbs-client-restore.yml`; pins in `vars/pbs-client.yml` | `pbs_client_runtime` (`docker` or `native`), `pbs_client_archives`, optional `pbs_client_stop_services` with `pbs_client_compose_project`, `pbs_client_backup_args`, `pbs_client_restore_args`, `pbs_client_quiesced_hook` |
| Kubernetes backup CronJob and restore Job | `tasks/kubernetes/recovery-backup.yml` and `recovery-restore.yml`, rendering `templates/kubernetes/recovery-*.yaml.j2` | `k8s_recovery`: `archives` (PVC `claim` with optional `owner`, or `config_map`), optional `database` step, `stop` and `restart` Deployments, optional `after_restore_tasks` hook |
| Kubernetes database dump, load and inspect step | `tasks/database/kubernetes-recovery-step.yml` | `k8s_database_step_engine` (`postgresql` or `mysql`), the instance and its config |
| Database dump, restore and drop | `tasks/database/*` (planned, part 04) | engine, database and login names |

The guest-side PBS client runs with `/tmp`, its cache and runtime files on a bounded tmpfs,
in a short-lived container on a Docker host or in a private mount namespace on a native
guest. Backup names each recovery point by its UTC time and keeps the previous point
through a same-day safety backup. A Restore preview (`overwrite=false`) runs the same
client path: it checks the point's archives and downloads its index without touching the
application. Stage backup sources and restore targets outside `/tmp`, beside the data
they replace.

## Local conventions

- Keep playbooks focused on orchestration. Put reusable behavior in a task file or role.
- Use `no_log` only on tasks that carry secrets; failures print stderr and stdout with secret values scrubbed.
- Preserve sibling keys when updating shared `homelabinfra_*` mappings. Follow
  `vars/CONTRACT.md` for merge and namespace rules.
- Treat `config/.generated/facts.yml` as generated topology, not a secret store.
- Keep application deployment and day-2 playbooks safe to run again.
