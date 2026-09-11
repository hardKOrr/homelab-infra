# Runner and control-plane recovery

This is the operator contract for recovering the Rundeck runner when the original runner
or Vaultwarden is unavailable. It is deliberately separate from the product adapters:
those own application data and assertions, while this document owns the control-plane
prerequisites and order. The shared method and destination contracts are [#77](https://github.com/hardKOrr/homelab-infra/issues/77)
and [#78](https://github.com/hardKOrr/homelab-infra/issues/78); the common acceptance
roll-up is [#80](https://github.com/hardKOrr/homelab-infra/issues/80).

## Recovery boundary

- This procedure preserves the source runner, Vaultwarden guest, PBS datastore and all
  existing recovery points. It does not authorize a production cutover, source shutdown,
  route change, credential rotation, source retirement or automatic cleanup.
- A failed or newly restored target stays stopped or on an isolated network until its
  VMID, address, hostname, SSH identity, PBS scope and service routes have been checked.
  A restored runner must not be allowed to drive production peers merely because it has
  a copied identity.
- Before replacing an existing target, record and independently verify its pre-restore
  recovery point. Keep that point until the restored target has passed verification and
  the recovery evidence has been recorded.
- Secrets and live authority belong in the separately controlled recovery record under
  [#77](https://github.com/hardKOrr/homelab-infra/issues/77), not in Git, this runbook,
  an issue comment, a job log, or a test fixture.

## What must be retained

The runner is a managed Debian 13 LXC with the `_+lab;_-debian;_rundeck` tags. Its normal
PBS guest backup is the first recovery artifact. Preserve the following as one mutually
consistent point-in-time set when execution history or an in-place restore is required:

| Scope | Retain | Why |
| --- | --- | --- |
| Rundeck database | `/var/lib/rundeck/data/` (including the installed database files) | jobs as imported, schedules, ACL/project metadata, execution state and history |
| Encrypted Key Storage | `/var/lib/rundeck/var/storage/` | encrypted `keys/` values, including secure job options and identities |
| Project configuration | `/var/lib/rundeck/projects/` when filesystem project storage is configured; otherwise the project definitions are in the Rundeck database under `/var/lib/rundeck/data/` | encrypted project configuration used when jobs start |
| Converter key | `/etc/rundeck/.storage-password`, as `root:rundeck` mode `0440` and an `EnvironmentFile` assignment | AES-256-GCM decryption for both `keys` and `projects`; one namespace without the other is not a recovery |
| Rundeck service config | `/etc/rundeck/rundeck-config.properties`, `/etc/rundeck/framework.properties`, `/etc/rundeck/realm.properties`, and `systemd` drop-ins | URL, converter declarations, authentication and service environment |
| Bootstrap handover | `/root/.rundeck-bootstrap` mode `0600` | package/version metadata, admin/API handoff and the non-secret recovery pointers; it is sensitive and root-only |
| Runner runtime | `/etc/homelab-infra/lab-run.env`, `state/vault-mode`, and the `config/` tree including `.backups/`, `.generated/` and `artifacts/` | checkout location/branch, mode, user configuration, topology, point-in-time config recovery and Get Config archives |
| Runner identity | `/var/lib/rundeck/.ssh/homelab-infra.pub`, the matching canonical `homelab-infra/runner` private key, and the tagged line in the PVE node's root `authorized_keys` | preserves the identity trusted by managed guests without putting the private half in ordinary runner state |
| Job option state | `/var/lib/rundeck/app-instances/` | current instance dropdowns; reproducible from `config/apps/*.yml`, but retain it when preserving the exact UI state |
| Evidence/logs | `/var/lib/rundeck/logs/` and the relevant `artifacts/` entries | execution evidence and operator records; do not treat logs as a secret store |

The paths above follow the Debian package layout described by Rundeck's
[system properties](https://docs.rundeck.com/docs/administration/configuration/system-properties.html)
and [storage facility](https://docs.rundeck.com/docs/administration/configuration/storage-facility.html)
documentation. The `projects` converter path is a logical storage namespace: do not assume
that a filesystem project directory exists when the configured project provider is a database.

The Key Storage tree and encrypted project configuration use the same
`RUNDECK_STORAGE_PASSWORD` value. The converter password is **not** a Vaultwarden item and
must not be stored in Key Storage: that would create the exact circular dependency this
procedure is meant to avoid. The independent recovery record must also contain the
automation account's `client-id`, `client-secret`, and `master-password`, the
Vaultwarden admin token, and the Proxmox/PBS access needed to reach the recovery point.
Keep the one-time Rundeck API token only if preserving the existing control-plane API
workflow; it is not needed to unlock Vaultwarden.

### Reproducible from Git

The following are reconstructed rather than backed up: `rundeck/bootstrap-rundeck.sh`,
`ansible/scripts/lab-run.sh`, Ansible playbooks/roles/tasks, `rundeck/jobs/*.yaml`,
`render-job.py`, `job-groups.yml`, `app-actions.yml`, `retired-jobs.yml`, the catalog,
`ansible/requirements.yml`, and the repository's pinned gate toolchain. Job definitions
are source-controlled, but their imported UUIDs and execution history live in Rundeck;
run **Reimport Jobs** after a Git reconstruction, and do not assume that restores history.
The venv, installed collections and Git checkout are also rebuildable from the recorded
branch and pins. Record the resolved commit in evidence even when the checkout is rebuilt.

### Explicit exclusions

After verified Vaultwarden Cutover, temporary `secrets.d/` seed files, `secrets.env`,
`BW_SESSION`, Bitwarden CLI app-data, and each execution's temporary SSH key are not
durable recovery inputs. They are intentionally removed or recreated per run. Before
cutover or during confirmed recovery, seed files are still required material and must be
preserved until exact Vaultwarden readback succeeds. Never back up a secret-shaped
`config/.generated/facts.yml` as a substitute for the canonical vault.

Do not rely on a runner backup alone for the PBS datastore. The PBS guest configuration
and its datastore path, datastore encryption/recovery keys where configured, API token,
retention and PVE registration are product-owned by [#110](https://github.com/hardKOrr/homelab-infra/issues/110).
The datastore needs an independent copy, remote, or other recovery path that remains
available when the PBS guest itself is lost. A PBS artifact that cannot be read from a
recovered datastore is not a usable runner recovery point.

## Dependency-ordered procedure

### 0. Establish independent authority and a recovery point

1. Stop ordinary automation by leaving the failed source inactive; do not delete it.
   Record source VMID, node, address, tags, checkout revision, package versions and the
   affected applications. Preserve the source disk and all existing backups.
2. From a workstation, PVE node, independent PBS endpoint, or offline recovery media
   that does not depend on the failed runner/vault, verify access to the selected PBS
   datastore and the exact runner artifact. Verify the artifact identity, completion and
   readable contents before touching a target.
3. Verify the independent recovery record contains the PBS/PVE access, converter password,
   automation-account credentials, Vaultwarden admin token and operator SSH/API path.
   Never put the converter password into the vault you need it to open. Do not rotate an
   existing token simply because the rehearsal has not started yet.

### 1. Recover PBS before depending on it

If PBS is the failed source, recover its VM/configuration and datastore through the
independent path owned by [PBS #110](https://github.com/hardKOrr/homelab-infra/issues/110)
first. Reattach or restore the datastore without formatting it, verify the PBS API and
the datastore's native artifact listing, then verify the PBS token and required
decryption material. Do not create a blank datastore with the same name and call it a
restore: it cannot satisfy the runner or Vaultwarden recovery point.

If PBS is healthy, still verify the selected artifact and the independent pre-restore
point before proceeding. The runner restore must not be the first test of PBS health.

### 2. Recover the runner to a new target

This is the source-independent route. It has two supported forms:

1. **Shared `pbs_guest` restore.** Use [Restore Guest](jobs/restore-guest.yaml) with
   `destination=new` and the runner's exact artifact. This route is available when a
   working control plane can invoke it; otherwise use the native PVE/PBS operator
   procedure from [#89](https://github.com/hardKOrr/homelab-infra/issues/89) from the
   independent recovery host. Select a free VMID, target storage and isolated address;
   restore stopped, and do not reuse the source address or start it on the production
   network.
2. **Git reconstruction.** On a new Proxmox target, run
   `rundeck/bootstrap-rundeck.sh` with `DEPLOY_VAULTWARDEN=0` and
   `RUNDECK_PACKAGE_VERSION_PIN` set to the recorded compatible package version. Set
   `PLATFORM_SSH_KEY_FILE` to the independently retained canonical private key; bootstrap
   fingerprints it and refuses a mismatch rather than generating a replacement. Restore
   the non-secret config, the independent Key Storage roots, converter file and required
   runtime metadata before enabling ordinary jobs. Install Ansible from
   `ansible/requirements.yml` and retain the resolved versions. This form rebuilds the
   runner from Git plus the recovery record; it does not recreate Rundeck execution
   history unless the database is restored as well.

For either form, check `pct config` or the equivalent target API before first start:
VMID, target address/MAC, node, storage, `_+lab` ownership and the runner public key must
be the intended values. Restore the `RUNDECK_STORAGE_PASSWORD` file and both converter
namespaces together. Restore `lab-run.env` with the new target's non-secret
`BW_SERVER`, and keep the target isolated until its connectivity checks pass.

### 3. Recover Vaultwarden without a secret/bootstrap cycle

Use the independent automation credentials and admin token only after the target runner's
Key Storage and converter password are readable. Then follow
[VAULTWARDEN-RECOVERY.md](VAULTWARDEN-RECOVERY.md): enter explicit Seed recovery, restore
the complete Vaultwarden guest/database rather than a blank replacement, verify HTTPS and
the two-account organization, and run Cutover only after exact readback. Caddy is the
edge prerequisite and its own state/route is owned by [Caddy #93](https://github.com/hardKOrr/homelab-infra/issues/93).

The recovery runner may run only the explicit recovery/enrollment/cutover operations while
Vaultwarden is unavailable. `lab-run.sh` must continue to fail closed for ordinary jobs;
recreated seed files do not bypass the `vault-mode` marker. Do not put an owner's master
password in a job option or log.

### 4. Re-establish the platform dependency order

After Vaultwarden Cutover succeeds, run a read-only status/config validation first. Then
reconcile only the isolated target in this order, verifying each recorded endpoint and
credential before the next dependent service:

1. Caddy and Vaultwarden route/state — [#93](https://github.com/hardKOrr/homelab-infra/issues/93)
   and [#123](https://github.com/hardKOrr/homelab-infra/issues/123).
2. Ntfy/notification delivery, then Authentik identity — Authentik state and assertions
   are owned by [#91](https://github.com/hardKOrr/homelab-infra/issues/91).
3. PostgreSQL, MariaDB, MySQL and Redis backends, each from its own retained state and
   credentials — [#112](https://github.com/hardKOrr/homelab-infra/issues/112),
   [#104](https://github.com/hardKOrr/homelab-infra/issues/104),
   [#106](https://github.com/hardKOrr/homelab-infra/issues/106), and
   [#117](https://github.com/hardKOrr/homelab-infra/issues/117).
4. Uptime/metrics and then dependent applications, using each product issue's recovery
   adapter and assertions. A product's process health alone is not evidence that its
   identity, database, storage or backup dependency was restored.
5. PBS configuration and scheduled coverage, if it was not already restored in step 1;
   keep this verification separate from proving an application restore.

The repository's `bootstrap.yml` is a deployment order for a healthy lab, not permission
to skip the recovery prerequisites above. It intentionally starts Caddy before
Vaultwarden, establishes the vault before ordinary services, and configures PBS only
after the baseline guests exist. No dependent job may run until its upstream state and
credential readback has passed.

## Both-destination acceptance

The shared `pbs_guest` method accepts both destination routes:

| Route | Required proof | End state |
| --- | --- | --- |
| New target | Restore a selected runner point with the source inaccessible; verify VMID/address/storage, converter access, job definition visibility and a non-destructive vault preflight | target remains stopped or isolated; no production route, duplicate identity or writer is activated |
| Existing target | Capture A, change the disposable runner to distinguishable B, preserve/verify the B pre-restore point, restore A, and inject a failure before retry | A is restored, B-only state is gone, the B point remains usable, and a failed target stays inactive with a deterministic retry path |

The existing-target rehearsal must use a disposable runner and disposable dependent guests.
The source runner is isolated from the restore operation, not shut down as a production
experiment. The B point is retained until the restored A state and dependent identity,
database and backup checks pass. Do not delete the disposable target automatically; record
its exact final state and handle cleanup as a separately authorized operation.

## Version and bootstrap compatibility

The current supported baseline is Debian 13, Java 21, Rundeck 6.x with AES-GCM support,
`ansible-core==2.18.*`, and the exact collection pins in `ansible/requirements.yml`.
`bootstrap-rundeck.sh` records the installed `RUNDECK_PACKAGE_VERSION`,
`RUNDECK_KEY_STORAGE_FORMAT=aes-256-gcm-v1`, and the resolved Ansible core version in the
root-only handover. A persisted database/key store must be restored with a compatible
Rundeck package and both converter declarations:

```properties
rundeck.storage.converter.1.path=keys
rundeck.storage.converter.1.config.passwordEnvVarName=RUNDECK_STORAGE_PASSWORD
rundeck.config.storage.converter.1.path=projects
rundeck.config.storage.converter.1.config.passwordEnvVarName=RUNDECK_STORAGE_PASSWORD
```

Pin the recorded package before restoring state; an unavailable pin is a stop condition.
Do not delete the converter file, invent a second password, regenerate the runner SSH
identity, or overwrite a product token to make a rehearsal green. If compatibility cannot
be demonstrated, leave the target inactive and report the exact mismatch and recovery
action.

## Evidence and live status

Repository evidence consists of the gate tests for this contract, `lab-run`'s fail-closed
mode/cleanup behavior, the shared guest recovery contract, and the package/converter
compatibility checks. Fixture evidence must name the synthetic source/target states and
artifact identifiers without secrets or backup contents. Live evidence, when an authorized
isolated destination exists, belongs in [#80](https://github.com/hardKOrr/homelab-infra/issues/80)
and must report source isolation, target identity/state, data handling, exact recovery
point, dependent identity/database/backup checks, and any failed-target retry action.

Neither gate success nor this runbook claims a live application restore. Cutover, source
retirement and cleanup remain separate operations.
