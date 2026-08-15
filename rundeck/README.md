# Rundeck

## Bootstrap

`bootstrap-rundeck.sh` is the whole of layer 1 (see the [root README](../README.md)). Copy
it to a Proxmox node and run it as root:

```sh
scp rundeck/bootstrap-rundeck.sh root@<node>:/root/
ssh root@<node> 'bash /root/bootstrap-rundeck.sh'
```

By default the command returns with the automation runner, Caddy, and HTTPS Vaultwarden
online. Open the URL it prints. The script already sent the enrollment invitations itself;
the **Vaultwarden Enrollment** job re-sends them and you click it only if that attempt
failed for want of DNS. Register both accounts in the web vault — **you choose the master
passwords there, nothing generates or prints them** — create the personal API key, stage
the three automation-account credentials in the named encrypted Key Storage entries, run
**Vaultwarden Cutover**, and only then run **Bootstrap Platform**. Password choice and
personal API-key creation are deliberately human Vaultwarden actions.

### What it does

| | |
|---|---|
| **Container** | Unprivileged Debian 13 LXC, nesting on, **tagged `homelab-infra`** |
| **Software** | OpenJDK 21, Rundeck 6, ansible-core 2.18 in a venv at `/opt/homelab-ansible`, the collections pinned in `ansible/requirements.yml` |
| **Repo** | cloned to `/var/lib/rundeck/homelab-infra`, tracking `origin/master` |
| **Proxmox credential** | creates the `homelab-infra@pve` user and a scoped `HomelabInfra` role, mints that user's API token, and writes the secret straight to Key Storage |
| **SSH identity** | generates an ed25519 keypair the platform reaches its guests with; public half into `config/proxmox.yml`, private half into Key Storage |
| **Config** | writes `config/proxmox.yml`, `config/infrastructure.yml` and `config/apps/rundeck.yml` |
| **Rundeck** | random admin password, non-expiring API token, the `homelab-infra` project, every job in `jobs/` imported, Key Storage staged |
| **Wiring** | `/etc/homelab-infra/lab-run.env` and a `/usr/local/bin/lab-run` symlink into the checkout |
| **Vaultwarden** | deploys Caddy first, then Vaultwarden and its HTTPS route; invites the exact owner/automation addresses with public signups disabled |

Everything is idempotent — re-running converges an existing container, rotates no
credential, and overwrites no answer you already gave. Override any default with an
environment variable (`VMID`, `CT_IP`, `CT_GW`, `CT_STORAGE`, `TEMPLATE`, `REPO_URL`,
`REPO_BRANCH`, …); see the header of the script.

`DEPLOY_VAULTWARDEN=1` is the default. Set `DEPLOY_VAULTWARDEN=0` only for a
runner-only recovery or diagnostic run. The shell script does not contain a second
Vaultwarden provisioner: it invokes the normal Ansible playbook, and later inventory
refreshes find `tag_vaultwarden` and reuse that guest.

### What it asks

The network/provider questions plus owner and automation email addresses, all defaulted except the domain and owner, and every one
also readable from an environment variable — so `NONINTERACTIVE=1` scripts the lot:

`LAB_DOMAIN`, `LAB_NET_CIDR`, `LAB_NET_GATEWAY`, `LAB_NET_DNS`, `LAB_TIMEZONE`,
`LAB_IP_OFFSET`, `LAB_REVERSE_PROXY`, `LAB_SSO`, `LAB_NOTIFICATIONS`, `LAB_DNS`,
`LAB_BACKUP_PATH`, `VAULTWARDEN_OWNER_EMAIL`, `VAULTWARDEN_AUTOMATION_EMAIL`.

Everything else is discovered from the node it runs on: the node name, the API address,
storages, bridges, template storage, the timezone.

Recovery handover values land in `/root/.rundeck-bootstrap` (0600) inside the container:
`pct exec <vmid> -- cat /root/.rundeck-bootstrap`.

**Debian 13 is not incidental.** `community.proxmox` 2.0.0 requires ansible-core >= 2.17,
which requires a Python 3.11+ controller. Debian 11 (Python 3.9) cannot run this codebase
at all.

### The Proxmox role

The script creates a role rather than reusing `root@pam`, with the privileges the
playbooks actually call:

| Group | Why |
|---|---|
| `VM.Allocate`, `VM.Clone`, `VM.Config.*`, `VM.PowerMgmt`, `VM.Console`, `VM.Monitor`, `VM.Migrate`, `VM.Snapshot*`, `VM.Backup` | creating, configuring, powering and destroying LXCs and VMs (`tasks/proxmox/lxc-create.yml`, `vm-create.yml`, `vm-clone.yml`) |
| `VM.Audit`, `Pool.Audit`, `Datastore.Audit`, `Sys.Audit` | the dynamic inventory and Lab Status enumerate guests and read node facts |
| `Datastore.AllocateSpace`, `Datastore.AllocateTemplate` | rootfs and disk allocation, and `pveam` template downloads |
| `Datastore.Allocate` | registering PBS as a storage backend (`tasks/bootstrap/configure-pbs.yml`) |
| `Sys.Modify` | the bridge on a new NIC, and the cluster-level vzdump backup job `configure-pbs.yml` creates |
| `SDN.Audit`, `SDN.Use` | bridge selection on PVE 8+; dropped automatically on releases that do not have them |

Be clear-eyed about the size of the reduction. The API token cannot manage users, realms,
permissions or ACLs, which is real — but `Sys.Modify` at `/` is broad because creating a
storage backend and a cluster backup job are cluster-configuration writes. Separately,
the existing provisioning contract delegates `pct`/`qm` readiness, DHCP-discovery and
sanitized-metadata commands to the PVE node over SSH. The bootstrap therefore authorizes
the platform's dedicated SSH identity for node root. The API credential is scoped; the
node-local command channel is not yet. The role is granted on `/` with propagation so the
platform can create guests on any node without the script enumerating them.

A token secret is displayed once, at creation, and can never be re-read. Re-runs therefore
keep the existing token; `ROTATE_PROXMOX_TOKEN=1` mints a new one.

## Jobs

`jobs/*.yaml` is the importable job set — one file per job, each a single script step that
runs one playbook. `bootstrap-rundeck.sh` imports them all over the REST API, so you do not
normally do this by hand. When you do:

```sh
for f in rundeck/jobs/*.yaml; do
  rd jobs load --project homelab-infra --format yaml --file "$f"
done
```

`rd` is not required — the REST API accepts the same YAML
(`POST /api/58/project/<p>/jobs/import?fileformat=yaml&dupeOption=update&uuidOption=preserve`).
Job UUIDs are stable, so re-loading updates the existing jobs instead of duplicating them.

| Group | Job | Playbook | Options |
|---|---|---|---|
| Bootstrap | Vaultwarden Enrollment | `playbooks/maintenance/vaultwarden-enroll.yml` | none |
| Bootstrap | Vaultwarden Cutover | `playbooks/maintenance/vaultwarden-cutover.yml` | none |
| Bootstrap | Bootstrap Platform | `playbooks/bootstrap.yml` | none |
| Config | Config Doctor | `playbooks/maintenance/config-doctor.yml` | none |
| Config | Configure App | `playbooks/maintenance/configure-app.yml` | `instance` + a dozen optional overrides + `extra_yaml` |
| Config | Get Config | `playbooks/maintenance/get-config.yml` | `instance` (optional), `archive` (optional) |
| Config | Reimport Jobs | — (calls the Rundeck API directly) | none |
| Apps | Deploy Vaultwarden | `playbooks/apps/vaultwarden.yml` | `instance` (prefilled `vaultwarden`) |
| Apps | Deploy Ntfy | `playbooks/apps/ntfy.yml` | `instance` (prefilled `ntfy`) |
| Apps | Deploy Caddy | `playbooks/apps/caddy.yml` | `instance` (prefilled `caddy`); optional encrypted Cloudflare Seed token and optional Vaultwarden credentials; the runner enforces whichever lifecycle state is active |
| Apps | Deploy Authentik | `playbooks/apps/authentik.yml` | `instance` (prefilled `authentik`) |
| Apps | Deploy Uptime Kuma | `playbooks/apps/uptime-kuma.yml` | `instance` (prefilled `uptime-kuma`) |
| Apps | Deploy Observability | `playbooks/apps/observability.yml` | `instance` (prefilled `observability`) |
| Apps | Deploy PBS | `playbooks/apps/pbs.yml` | `instance` (prefilled `pbs`) |
| Apps | Remove App | `playbooks/apps/remove.yml` | `instance`, `app` (optional), `delete_data` |
| Maintenance | Lab Status | `playbooks/maintenance/status.yml` | none |
| Maintenance | Check Native App Updates | `playbooks/maintenance/check-native-updates.yml` | none — cron `0 0 6 ? * MON *` |
| Maintenance | Restart App | `playbooks/maintenance/restart-app.yml` | `instance` |
| Maintenance | Tail App Log | `playbooks/maintenance/tail-applog.yml` | `instance`, `lines` |
| Maintenance | Rollback Container | `playbooks/stacks/rollback-container.yml` | `stack`, `container`, `image_tag` (optional) |
| Maintenance | Wire Media Stack | `playbooks/stacks/wire-media-stack.yml` | none |
| Maintenance | Vaultwarden Recovery | `playbooks/maintenance/vaultwarden-recovery.yml` | exact break-glass confirmation |

**One job per app, still no typing.** Each Deploy job carries a required `instance`
option whose *value is already the app's own name*, so Rundeck prefills it and the normal
deployment stays one click. Changing it is how a second instance of the same app is
deployed alongside the first — a second estate's Authentik, per the estate contract that
an estate's SSO is an ordinary app deploy. The instance name is the
`config/apps/<instance>.yml` filename, the guest hostname and the subdomain.

Copying a job file to get a second instance is no longer necessary, and the copy is worse:
its UUID has to be changed by hand and it drifts from the original on every later edit.

### Every step is one `lab-run` call

No job file contains a path, a venv, or a `cd`. A step looks like this:

```bash
#!/bin/bash
set -euo pipefail
exec lab-run playbooks/apps/caddy.yml -e "instance=$RD_OPTION_INSTANCE"
```

`lab-run` is `/usr/local/bin/lab-run`, a symlink into the checkout at
`ansible/scripts/lab-run.sh` — so the wrapper itself ships in the repo and arrives via the
clone; only the symlink and `/etc/homelab-infra/lab-run.env` live on the host. It:

1. resolves `LAB_REPO`, `LAB_VENV` and `LAB_BRANCH` from that env file
2. permits temporary seed files only for the explicit Caddy/Vaultwarden enrollment,
   cutover, and recovery playbooks before the durable marker exists
3. in Vault mode, logs in and unlocks the dedicated automation account, synchronizes all
   canonical items, and writes the runner SSH key only to execution-private state
4. **refreshes the checkout** to `origin/$LAB_BRANCH` and echoes the resolved commit into
   the job log, then re-execs itself from the refreshed copy
5. runs `config-doctor` — a missing key fails here, at the front door
6. runs `ansible-playbook` through `with-proxmox-env.sh`, then locks/logs out and removes
   all temporary CLI/session state while preserving the playbook exit status

This is why a fix pushed to the repo is executed by the next click with no human action,
and why a change to *how* jobs run is one edit rather than eighteen. That lesson was paid
for: the first live run failed identically in all 15 jobs, and the fix landed in the one
shared wrapper (commit `059316a`).

Escape hatches, per job or per shell: `LAB_REFRESH=0` runs the on-disk checkout unchanged
(for pinning while debugging), `LAB_DOCTOR=0` skips the config check (the three Config
jobs set this themselves — they exist to diagnose and fix a config the doctor is
complaining about).

**The refresh is off unless a runner turned it on.** It is a `git reset --hard`, so
`lab-run.sh` defaults `LAB_REFRESH` to 1 only when `/etc/homelab-infra/lab-run.env` exists
— which only `bootstrap-rundeck.sh` creates — and to 0 everywhere else, including a
developer's working tree. It also refuses outright if the checkout has uncommitted tracked
changes. A bootstrapped runner never has any: its checkout is only ever written by git.

Job options reach the script as `RD_OPTION_*` environment variables rather than
`${option.x}` tokens, which would collide with shell expansion.

### Playbooks refresh themselves; job definitions do not

The checkout refresh keeps the **playbooks** current. **Job definitions** live in
Rundeck's database and only change when something imports them. Run **Reimport Jobs**
after a change to `rundeck/jobs/*.yaml` reaches the tracked branch — a new job, a new
option, a changed description. Playbook changes never need it.

**The git SCM plugin is deliberately not used.** It syncs job definitions and nothing
else — it was never going to refresh the working tree the steps execute, which is the
problem people reach for it to solve. It also makes UI edits and repo imports fight each
other. One-way import from the repo is the only path here, and jobs are not expected to be
edited in the UI; if you edit one there, the next Reimport Jobs overwrites it.

## Credentials

`config/proxmox.yml` carries connection shape only. Before cutover, the bootstrap script
uses minimum temporary seed files. After exact Vaultwarden readback, the files are removed
and ordinary jobs cannot read replacements.

| Where | What | Role |
|---|---|---|
| Key Storage `keys/project/homelab-infra/vaultwarden-machine/client-id` | automation API client ID | secure option on vault-backed jobs |
| `.../client-secret` | automation API client secret | secure option on vault-backed jobs |
| `.../master-password` | automation master password | secure option on vault-backed jobs |
| `.../admin-token` | Vaultwarden server administration token | enrollment/cutover only |
| `keys/project/homelab-infra/rundeck/api-token` | Rundeck API token | job import/cutover only |
| `keys/project/homelab-infra/bootstrap/cloudflare-api-token` | temporary token scoped to Zone Read and DNS Edit | Caddy Seed bootstrap and cutover; deleted after exact Vaultwarden readback |

Rundeck OSS exposes Key Storage values to scripts through secure job options. Source job
files stay uncluttered: `render-job.py` centrally adds the required project-scoped options
at import. The wrapper consumes only their `RD_OPTION_*` environment variables; neither
secret interpolation nor ordinary option values are used.

The full `/keys` tree uses Rundeck 6.0's AES-256-GCM converter. Its password file is
`/etc/rundeck/.storage-password` (`root:rundeck`, `0440`) and the generated handover copy
must be backed up separately. Encryption protects stored values; root on a running runner
can still access the converter key and job memory.

The pre-cutover `keys/proxmox/api-token`, `keys/rundeck/homelab-ssh`, and Cloudflare
bootstrap entries are imported into their canonical Vaultwarden items, verified, then
deleted. Recovery is documented in [VAULTWARDEN-RECOVERY.md](VAULTWARDEN-RECOVERY.md).

### Rundeck API token rotation

The Rundeck control-plane token has exactly two persisted consumers:

- `RUNDECK_API_TOKEN` in `/root/.rundeck-bootstrap` inside the Rundeck container
- `keys/project/homelab-infra/rundeck/api-token` in Rundeck Key Storage

The checkout `.env` contains only `RUNDECK_URL` and `RUNDECK_PROJECT`. Do not add the token
there. Rotate in this order so the current token remains the rollback path until the
replacement is proven:

1. Create a replacement `homelab-infra` token and retain its token ID and one-time secret.
2. Replace the bootstrap-file value without changing its ownership or `0600` mode.
3. Replace the Key Storage value.
4. Confirm the replacement can read the `homelab-infra` project.
5. Run **Reimport Jobs**. Its successful import proves the secure option received the new
   Key Storage value and could authenticate back to Rundeck.
6. Delete the old token by ID, then confirm it is rejected while the replacement still
   authenticates.

This sequence ran live on 2026-08-14. Reimport Jobs execution 141 succeeded using the
replacement; the old token then returned HTTP 403 and the replacement remained valid.

## Reading and changing config from the UI

No SSH session appears anywhere in this document's happy path, and that is the point.
`config/` lives on one host; before the **Config** job group existed, the only way to see
or change it was a text editor over SSH on a machine whose contents existed nowhere else.

- **Configure App** writes `config/apps/<instance>.yml` from a form. Fields are overrides;
  blank keeps the current value. `extra_yaml` covers anything the form does not name.
- **Get Config** reads the set back out, redacted, and writes an unredacted `tar.gz`
  restore point under `artifacts/` on the runner. Rundeck OSS serves no artifacts, so fetch
  that file over scp if you want it off the host — or rely on PBS, which backs up the runner
  along with it.
- **Config Doctor** validates everything and names each problem by file and key path.

History is point-in-time: every write is copied to `<dir>/.backups/<file>.<timestamp>`
first, pruned to the newest 20, and the diff goes into the job log. That answers "what did
this look like before" and "get it back". It does not answer "who changed this and why" —
an accepted trade for a homelab lab definition.
