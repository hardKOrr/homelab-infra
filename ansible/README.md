# Ansible implementation

This directory contains the provisioning and operating implementation. It must remain
independent of Rundeck and other operator interfaces. Operator interfaces select and run
playbooks; they do not define Ansible behavior.

## How a run executes

A Rundeck job calls `lab-run`, the installed entry point for
`ansible/scripts/lab-run.sh`. The wrapper reads its runner environment from
`/etc/homelab-infra/lab-run.env` (or `LAB_ENV_FILE`). With `LAB_REFRESH=1`, it
refreshes the checkout to `origin/$LAB_BRANCH` and re-executes the refreshed wrapper;
refresh defaults on for a configured runner and off for a local checkout. `LAB_DOCTOR`
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
run.

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

## Live operations

Live runs are serial across the whole lab. Before changing live state, take the
operator's lab lock as the private access instructions describe, and confirm that no
Rundeck execution or node-side bootstrap is running. Hold the lock until the live work is
done.

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
`delete_data=true` and overwrite Restore. Go straight through. Stop only for an ownership
mismatch, a safety guard that trips, or a failure the issue's scope cannot fix. Read
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
5. Remove with `delete_data=true`. Verify the instance, its data and its wiring are gone,
   the backup remains, and other applications are unaffected. The stack host stays;
   Remove never destroys a guest.
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

After the drill, when no PR is needed, post the result comment and close the issue.

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

## Local conventions

- Keep playbooks focused on orchestration. Put reusable behavior in a task file or role.
- Preserve sibling keys when updating shared `homelabinfra_*` mappings. Follow
  `vars/CONTRACT.md` for merge and namespace rules.
- Treat `config/.generated/facts.yml` as generated topology, not a secret store.
- Keep application deployment and day-2 playbooks safe to run again.
