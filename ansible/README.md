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

### Moving PBS to another node with its datastore

Use this when the PBS VM's node is unavailable but its separate ZFS datastore can be made
available to another Proxmox node. Deploy PBS in `reuse` mode so it attaches the existing
volume without formatting it. Fence a failed source node before moving or importing its
drives. Never let two PBS guests write to the source and received copies at the same time.

1. Verify that no backup, prune, or garbage-collection task is writing to PBS. Record the
   PBS backup groups and snapshot identifiers. Identify the datastore volume from
   `config/apps/pbs.yml`: `proxmox.vm.datastore_disk.storage` plus its reserved
   `source_vmid` gives
   `<pve-storage>:vm-<pbs-datastore-vmid>-disk-1`. Keep `source_vmid` unchanged; it must
   not be a guest VMID.
2. For a physical drive move, power off and fence the failed node before moving every disk
   that belongs to the ZFS pool. On the recovery node, import the pool without mounting
   filesystems, for example `zpool import -N <zfs-pool>`. Verify the pool identity and
   confirm the Proxmox storage ID is enabled for the recovery node and maps to that pool.
   Never force-import a pool while the source node can still write to it.
3. For a copy-based drill while the source is still available, resolve the exact volume
   `<pve-storage>:vm-<pbs-datastore-vmid>-disk-1`. With node-local `zfspool` storage,
   `pvesm path <pve-storage>:vm-<pbs-datastore-vmid>-disk-1` should resolve to
   `/dev/zvol/<zfs-pool>/vm-<pbs-datastore-vmid>-disk-1`; snapshot and send that zvol to the
   matching destination pool. The received zvol must keep the same
   `vm-<pbs-datastore-vmid>-disk-1` name so Proxmox exposes the same volume ID. For example,
   after replacing the placeholders with the paths from `pvesm path` and the Proxmox storage
   definition:

   ```sh
   set -o pipefail
   ssh root@<pve-node-1> zfs snapshot '<zfs-pool-1>/vm-<pbs-datastore-vmid>-disk-1@<zfs-snapshot>'
   ssh root@<pve-node-1> zfs send -p '<zfs-pool-1>/vm-<pbs-datastore-vmid>-disk-1@<zfs-snapshot>' |
     ssh root@<pve-node-2> zfs receive -u '<zfs-pool-2>/vm-<pbs-datastore-vmid>-disk-1'
   ```

   A Proxmox storage of type `zfs` is ZFS over iSCSI; its `pvesm path` is an iSCSI locator,
   not a local ZFS dataset path. For a copy from that backend, snapshot and send the zvol
   from its configured ZFS portal, then receive it into a node-local `zfspool`. Set
   `proxmox.vm.datastore_disk.storage` to that destination storage ID before Deploy PBS.
   For a physical move into the same local pool and storage ID, keep the existing storage ID.

   Confirm on the recovery node that `pvesm list <pve-storage> --vmid <pbs-datastore-vmid>`
   shows exactly one matching volume and its ZFS volume size matches the source. For a copy
   to a different storage, use its destination storage ID here. Keep the source volume
   untouched as the rollback copy.
4. Stop the old PBS guest and remove only that owned guest while preserving its separately
   owned datastore volume. For a still-reachable source, use Remove App with
   `delete_data=true` and `destroy_guest=true`; its ownership checks require the exact
   `_+lab` and `_pbs` tags. Do not continue if the datastore volume is still owned by the
   guest VMID instead of the reserved `source_vmid`:

   ```sh
   "$HOMELAB_LAB_DIR/runner-lab-run.sh" <branch> playbooks/apps/remove.yml \
     -e instance=pbs -e delete_data=true -e destroy_guest=true
   ```

   If the source Proxmox node is offline, fence it at the power level and verify `pvecm
   status` reports quorum from a surviving node. Do not bypass quorum. This stale-config
   procedure is for a non-HA PBS guest; if Proxmox HA manages it, follow the HA recovery
   procedure and confirm it cannot restart against the source datastore before continuing.
   Before retiring the stale PBS guest registration, inspect the exact
   `/etc/pve/nodes/<pve-node-1>/qemu-server/<GUEST-vmid>.conf`: its name and tags
   must identify this PBS guest, and the datastore disk must use the reserved
   `source_vmid`. Back up that one config file, then remove only that stale guest config
   with `rm -- /etc/pve/nodes/<pve-node-1>/qemu-server/<GUEST-vmid>.conf`. This removes
   only the stale VM registration; leave its disks and the retained zvol untouched. PBS's
   OS disk may be local to the failed node, so do not move the old VM config to the recovery
   node unless every disk and local resource it references is available there. Follow
   [Proxmox's failed-node recovery guidance](https://pve.proxmox.com/pve-docs/chapter-pmxcfs.html#_recovering_moving_guests_from_failed_nodes)
   for the cluster-side ownership rules. If quorum, fencing, ownership or the retained
   volume identity is uncertain, stop and restore the source node first.
5. In the private `config/apps/pbs.yml`, set `proxmox.node` to the recovery node, keep the
   selected datastore storage ID and reserved `source_vmid`, and set
   `app.datastore_mode: reuse`. A per-instance node must already be present in
   `proxmox.nodes`. Run **Deploy PBS**; its volume preflight checks the selected node before
   creating the guest, then attaches the existing disk and registers the existing
   datastore without initialising it:

   ```sh
   "$HOMELAB_LAB_DIR/runner-lab-run.sh" <branch> playbooks/apps/pbs.yml -e instance=pbs
   ```
6. Confirm PBS lists the baseline groups and snapshots. Restore an application canary from
   one of those snapshots and verify the application, then verify the refreshed PVE PBS
   storage fingerprint and managed guest VMID list. Run the scheduled backup job and
   confirm it writes a new snapshot to the moved datastore.
7. Keep the original volume until the application restore and a new scheduled backup have
   both passed. If Deploy fails, stop the replacement before changing volume attachment or
   configuration; the source volume remains the recovery point.

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
For those new implementations, `restore.yml` replaces state through the shared
[`tasks/recovery/restore-swap.yml`](tasks/recovery/restore-swap.yml), which moves the
original aside on the same filesystem and puts it back on any later failure, as
[`roles/tautulli/tasks/restore.yml`](roles/tautulli/tasks/restore.yml) does. App-specific
checks go in its hooks, with a gate test that runs them.
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
starts it. An empty ARL supports settings-only use; the human
route still uses its declared Authentik access path. Deploy preserves an existing
or restored `.arl` when the configured ARL is empty, and publishes that effective
value to the canonical media credential for Lidarr. A non-empty configured ARL
rotates the file and restarts Deemix.

Plex native recovery captures its stopped service's server identity, library databases,
Metadata and Media as one PBS `data.pxar` in `host/<instance>`. Cache, Codecs, Crash
Reports and Logs are excluded; external media mounts and transcode are outside the
recovery unit. Restore requires `overwrite=true`, accepts only snapshots in the selected
`host/<backup id>` group, refuses a state path that overlaps declared media or transcode,
swaps the state directory through the shared restore swap, and publishes the restored
server token to `homelab-infra/media/<instance>` after an authenticated API check. A
failure puts the original state back and starts it.

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
restore puts the original directory and Compose API-key overrides back and starts it.

Bazarr native recovery stops only the selected service and archives its config directory
as one PBS `data.pxar` in `host/<instance>`, excluding `log` and `cache`. Backup retains
the two newest snapshots alongside daily retention, preserving the selected recovery
point when the fresh instance is backed up. Restore previews until `overwrite=true`,
checks the source group, and retains the original directory on the same filesystem until
an authenticated settings check and canonical API-key publication succeed. A failure
puts the original state back and starts it. The final Deploy keeps the
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
item. A failure puts the original directory back and starts it. Deploy keeps
the recovered INI, API key, categories and application settings.

qBittorrent native recovery stops only the selected service and archives its config,
preferences and torrent resume metadata as one PBS `data.pxar` in `host/<instance>`.
Logs and downloads are excluded; payloads remain on the external media mount. Backup
keeps the two newest snapshots alongside daily retention. Restore previews until
`overwrite=true`, validates the backup group and staged configuration, and retains the
original config on the same filesystem until the canonical WebUI credential works.
Failure puts the original state back and starts it. Deploy preserves
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

#Forgejo defaults to Docker on its services VM. Set `hosting.method: kubernetes` in the
instance config to use the cluster, retaining its registered PostgreSQL backend. Its
`app.storage` GiB claim uses the cluster's default StorageClass. HTTP uses the instance's
usual hostname through the ingress; Git SSH advertises the first cluster node's address
and `app.ssh_node_port` (default 30222). A second instance needs an unused SSH NodePort;
Deploy checks the cluster-wide claim before applying it. An enabled SSH forward targets
that node and must set `local_port` to `app.ssh_node_port`. SSH is separate from the HTTP route.

Both Forgejo methods keep the same `data.pxar` layout: `forgejo/` contains the complete
application data and `database/forgejo.dump` contains its registered PostgreSQL database.
Kubernetes quiesces Forgejo through the shared backup Job, dumps the database through the
shared client, and copies that dump into the archive layout before upload. Restore reads
that nested dump through the shared recovery Job, allowing recovery points to move between
Docker and Kubernetes without regenerating Forgejo's application secret key.

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
