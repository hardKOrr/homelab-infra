# Rundeck

## Bootstrap

`bootstrap-rundeck.sh` is the whole of layer 1 (see the [root README](../README.md)). Copy
it to a Proxmox node and run it as root:

```sh
scp rundeck/bootstrap-rundeck.sh root@<node>:/root/
ssh root@<node> 'bash /root/bootstrap-rundeck.sh'
```

For the first-time operator path, including Vaultwarden enrollment and cutover, follow the
[root README](../README.md). This document owns the Rundeck implementation and maintenance
contracts.

### What it does

| | |
|---|---|
| **Container** | Unprivileged Debian 13 LXC, nesting on, **tagged `_+lab;_-debian;_rundeck`** |
| **Software** | OpenJDK, Rundeck, and the pinned Ansible toolchain in `/opt/homelab-ansible` |
| **Repo** | cloned to `/var/lib/rundeck/homelab-infra`, tracking `origin/master` |
| **Proxmox credential** | creates the `homelab-infra@pve` user and a scoped `HomelabInfra` role, mints that user's API token, and writes the secret straight to Key Storage |
| **SSH identity** | generates an ed25519 keypair the platform reaches its guests with; public half into `config/proxmox.yml`, private half into Key Storage |
| **Config** | writes `config/proxmox.yml`, `config/infrastructure.yml` and `config/apps/rundeck.yml` |
| **Rundeck** | random admin password, non-expiring API token, the `homelab-infra` project, every job in `jobs/` imported, Key Storage staged |
| **Wiring** | `/etc/homelab-infra/lab-run.env` and a `/usr/local/bin/lab-run` symlink into the checkout |
| **Vaultwarden** | deploys Caddy first, then Vaultwarden and its HTTPS route; generates both master passwords (owner's handed to root in `/root/.rundeck-bootstrap`), runs Vaultwarden Enrollment and Vaultwarden Cutover, then Bootstrap Platform — resuming from the `vault-mode` / `cutover-complete` markers on a re-run |

Everything is idempotent — re-running converges an existing container, rotates no
credential, and overwrites no answer you already gave. Override any default with an
environment variable (`VMID`, `CT_IP`, `CT_GW`, `CT_STORAGE`, `TEMPLATE`, `REPO_URL`,
`REPO_BRANCH`, `CT_DNS`, `DEPLOY_VAULTWARDEN`, `RUNDECK_PACKAGE_VERSION_PIN`,
`PLATFORM_SSH_KEY_FILE`, `ANSIBLE_CORE_SPEC`, …); see the header of the script.

Vaultwarden identity inputs are first-run answers, not identity-change controls.
Both direct bootstrap Enrollment and the Rundeck Enrollment job use the preserved
`config/infrastructure.yml` declaration: `vaultwarden.owner_email` and
`vaultwarden.automation_email` (default `homelab-infra@<domain>` when absent).
A nonempty `VAULTWARDEN_OWNER_EMAIL` or `VAULTWARDEN_AUTOMATION_EMAIL` must match
that effective declaration exactly. A conflict stops bootstrap before enrollment,
or stops Enrollment before admin authentication or invitations. Unset the conflicting
input or align it with the preserved declaration, then retry. An undeclared owner
must be declared through the supported configuration path first; an environment
input cannot supply a different identity for just one invocation. The check does
not rewrite configuration, rotate credentials, or reconcile existing accounts or
organizations. Changing the declared identity requires a separate explicit decision
and recovery plan; do not change it merely to bypass a conflict on a rerun.

Runner sizing inputs (`CT_CORES`, `CT_MEMORY`, `CT_SWAP`, `CT_DISK`) apply to a
**new** guest: defaults are 4 cores, 8192 MB RAM, 512 MB swap and 16 GiB rootfs.
On a rerun, bootstrap reads the selected guest's current PVE sizing. An explicit
sizing input must match that sizing or bootstrap stops before container convergence;
it never resizes an existing runner or describes an unapplied resize. Pending PVE
changes are not treated as current resources. A guest with unrestricted cores or
an undiscoverable rootfs size is rejected rather than assigned fictitious sizing.

`config/apps/rundeck.yml` is a self-description, not a resize request. Bootstrap
refreshes its guest identity, sizing, rootfs storage and `net0` fields from current
PVE, including bridge/VLAN rather than fresh defaults. It preserves unrelated
`proxmox` keys, existing `app`/`routing` answers and other user-owned sections;
missing application/routing defaults are filled in. An unchanged declaration is
not rewritten. When guest facts need correction, YAML formatting/comments may be
normalized, but unrelated authored values remain. Bootstrap does not move an
existing guest by changing identity/network/storage environment inputs.

Bootstrap reruns preserve `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`,
and `NODE_EXTRA_CA_CERTS` already declared in `/etc/homelab-infra/lab-run.env`.
Each must name an existing absolute CA file; unrelated environment entries are not
copied. `lab-run` exports them to every run unless the caller already set them (bootstrap's
Seed steps reach it through `sudo`, which drops the caller's environment), and warns and
skips them if one is unusable. This supports private or staging certificate authorities
without disabling TLS verification or changing system trust.
The installed `lab-run` symlink resolves its source checkout before loading the CA
helper, including Seed runs with checkout refresh disabled.
Bootstrap's Vaultwarden HTTPS readiness check uses the same validated declarations
inside its root `pct exec` context, retaining nonempty caller CA settings. Invalid
declarations or unsuccessful verified HTTPS keep enrollment pending. The check reads
only CA declarations and changes neither credentials nor system trust.

`DEPLOY_VAULTWARDEN=1` is the default. Set `DEPLOY_VAULTWARDEN=0` only for a
runner-only recovery or diagnostic run. The shell script does not contain a second
Vaultwarden provisioner: it invokes the normal Ansible playbook, and later inventory
refreshes find `tag_vaultwarden` and reuse that guest.

A recovery rebuild can set `RUNDECK_PACKAGE_VERSION_PIN` to the exact Debian package
version recorded in the handover. The bootstrap verifies that version and records the
installed Rundeck and Ansible versions plus the AES-GCM storage format. This makes the
compatibility boundary explicit before persisted database or encrypted Key Storage state
is trusted; it does not rotate or replace any identity or decryption material.

The database, encrypted Key Storage, converter password, runner identity, and execution
evidence are not reproducible from this checkout. The independent retention and restore
contract is [RUNNER-RECOVERY.md](RUNNER-RECOVERY.md); it also covers the shared
[new/existing guest restore](jobs/restore-guest.yaml) route and the offline route when the
original runner is unavailable.

The new runner's resolver is set explicitly before its first boot: `CT_DNS`, then
`LAB_NET_DNS`, then `CT_GW`. This prevents a runner on a service VLAN from inheriting
a management-only resolver from the Proxmox host. Existing containers keep their DNS
configuration; Proxmox stages DNS changes to a running container for its next restart.

### What it asks

The script asks for the lab network, provider choices, and owner and automation identities.
Each prompt also has an environment-variable form, so `NONINTERACTIVE=1` supports scripted
bootstrap. The script header owns the current input list and defaults.

Outbound mail is optional: `LAB_MAIL_PROVIDER` defaults to `none`. Owner and automation
addresses may be synthetic; enrollment does not require a monitored mailbox or working
mail. Selecting `smtp` asks for `LAB_MAIL_HOST`, `LAB_MAIL_PORT` (default `587`),
`LAB_MAIL_ENCRYPTION` (default `starttls`), `LAB_MAIL_FROM_ADDRESS`,
`LAB_MAIL_FROM_NAME` (default `Homelab`), and `LAB_MAIL_USERNAME` (default From address).
Only these nonsecret fields enter `config/infrastructure.yml`.
Bootstrap SMTP intake requires an AUTH username and password. Unauthenticated internal
relays are outside this intake; keep `LAB_MAIL_PROVIDER=none` rather than inventing a
credential.

The SMTP password is a hidden prompt, or `LAB_MAIL_PASSWORD` for unattended input. Load
it from a root-owned `0600` environment file rather than putting a credential in command
arguments, shell history or job options. Run without shell tracing:

```sh
set +x
set -a
source /root/bootstrap-private.env
set +a
NONINTERACTIVE=1 bash /root/bootstrap-rundeck.sh
```

Bootstrap stages the single-line password only in
`/etc/homelab-infra/secrets.d/mail.env` (`0600 rundeck:rundeck`, directory `0700` with the
same owner). The Seed wrapper reads it literally, without executing its contents; the
existing cutover imports it into `homelab-infra/mail` (`password`). Reruns preserve authored
config and an existing sink, even if different environment answers are supplied. A
missing sink for an already configured SMTP provider can be supplied before cutover;
after the vault-mode marker exists bootstrap neither prompts nor restages it. After
cutover use **Store Secret** with item `homelab-infra/mail`, field `password` for new or
rotated input, and the supported configuration path for nonsecret changes. No mail is
sent or provider account validated by this intake.

Everything else is discovered from the node it runs on: the node name, the API address,
storages, bridges, template storage, the timezone.

Recovery handover values land in `/root/.rundeck-bootstrap` (0600) inside the container:
`pct exec <vmid> -- cat /root/.rundeck-bootstrap`.

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

`jobs/*.yaml` is the source job set. An ordinary source file defines one job; an action
template expands into one job per applicable application. Each rendered job has a single
script step that runs one playbook. `bootstrap-rundeck.sh` and **Reimport Jobs** validate
and render every source before posting it to the REST import endpoint. Job UUIDs are stable
and imports use `dupeOption=update&uuidOption=preserve`, so a group change updates the
existing job and keeps its execution history and schedule.

Three files own classification:

- `../catalog/applications.yml` classifies every deployable application by human purpose and
  application type. The renderer projects each one to a leaf folder named after the
  application: `<root>/<category>[/<type>]/<name>`.
- `app-actions.yml` names the day-2 action templates and the hosting kinds each is
  implemented for. The renderer expands each template into one job per application it
  applies to, under `<that application's folder>/Maintenance`.
- `job-groups.yml` classifies the jobs that act on the lab rather than on one application.

The source job keeps the projected `group:` for reviewability; a template declares the
literal placeholders `'%GROUP%'` and `'%UUID%'` instead, so it can never be mistaken for a
job and imported as itself. Before any import, `render-job.py --check` rejects a missing or
extra job, duplicate classification, stale group, an application name that no longer matches
its catalog entry, a template that no application selects, and any UUID collision across the
whole expanded set. Hosting kind, stack, dependencies, and other execution details do not
control navigation.

The top-level tree is:

| Root | Purpose |
|---|---|
| `Applications` | Browse optional applications by purpose, type, and name |
| `Platform` | Deploy access, identity, monitoring, backup, and hosting capabilities |
| `Manage` | Lab-wide configuration, integration, health, and storage actions |
| `Operate` | Read-only diagnosis and operator troubleshooting actions |
| `Recover` | Recover guests and credentials; run bounded recovery proofs |
| `Setup` | Establish credentials, bootstrap the platform, and reload automation definitions |

Run `python3 rundeck/render-job.py --check rundeck/jobs` to validate the complete tree.
Normally, use **Reimport Jobs** rather than importing an individual raw source file: the
renderer also injects the secure Key Storage options required by that job.

### Description style

Rundeck renders both job descriptions and workflow option descriptions as Markdown. Keep
the source useful at a glance:

- Lead with the outcome. Use short headings or bullets only when they separate real choices,
  phases or effects.
- Put exact values, paths and option names in backticks. Use bold labels for defaults,
  boundaries and destructive effects.
- Keep option help beside the decision it explains. State what blank or each enumerated value
  does; do not repeat the job description.
- Use a YAML block scalar for multiline Markdown. Stay within ordinary Markdown syntax that
  renders consistently; MarkDeep-specific features are not required.

### One folder per application

Everything an operator does to one application is in that application's own folder: its
Deploy job and a `Maintenance` folder with its implemented day-2 jobs.

The renderer resolves values the platform already knows from
`../catalog/applications.yml` and `../ansible/vars/app-defaults/<app>.yml`:

| Was typed | Now |
|---|---|
| `instance` | estate-aware default, and offered as a live dropdown on Deploy and Maintenance jobs |
| `app` (when an instance is named differently) | baked into the step |
| `stack` (Rollback) | resolved from the instance override over application defaults |
| `source_config_path` (Migrate) | defaulted to `/var/lib/<app>` |

`app-actions.yml` declares which actions apply to each hosting kind and any application
exceptions. The renderer reads an explicit `hosting:` first; otherwise the presence of
`stack:` distinguishes Docker from native LXC. Catalog `extra:` entries opt an application
into an action that hosting kind alone does not select.

An action absent from a folder is absent because it is not implemented safely. Most Docker
apps rely on the shared guest's PBS backup and therefore get no per-application Backup or
Restore action. An application may explicitly replace the derived action set when its role
owns a truthful recovery seam — Actual Budget archives and restores its quiesced /data
mount without touching sibling services. There is no Rollback for a Kubernetes workload.
Authentik and Observability also exclude Rollback: each is a multi-service Compose project,
while the generic rollback seam pins one image. A button that misstates what it changed is
worse than no button.

An application marked `essential:` in the catalog gets no Remove job. The platform does not
offer a one-click removal of the reverse proxy or the vault every other job depends on.

### Instances are a dropdown, not a memory test

Each application job's `instance` option has a one-click default and offers every instance
of that application from
`/var/lib/rundeck/app-instances/<app>.json`. `../ansible/scripts/app-instances.py` rewrites
those files before and after every job from the `config/apps/*.yml` that exist right then,
so a second instance created by Configure is offered when the operator opens the next job
without a reimport. Bootstrap creates the directory and its files owned by `rundeck`, the
account that runs job steps, so later list changes remain writable.

In a single-estate lab, the instance form is `<app>[-<variant>]` (`radarr`, `radarr-4k`).
When `domains:` declares two or more estates, exactly one estate must be the explicit
default and every estate-scoped instance is `<app>-<estate>[-<variant>]`
(`app-estate`, `app-estate-variant`). The dropdown labels include the estate. The
longest matching application slug wins, so `uptime-kuma` is not read as an instance of
`uptime`.

Only applications the catalog marks `scope: estate` are suffixed. A `scope: lab` service
keeps its ordinary name because one deployment serves every estate.

**The suffix never reaches a URL.** Every routed application declares `routing.subdomain`
in `../ansible/vars/app-defaults/<app>.yml`, so `<app>-<estate>` can publish
`<subdomain>.<estate-domain>`. Without that declaration the hostname falls back to the
instance name and the estate suffix would show up in the address; the gate rejects a routed
application that omits it.

Copying a job file to get a second instance is not necessary, and the copy is worse: its
UUID has to be changed by hand and it drifts from the original on every later edit.

### Withdrawing a job

`retired-jobs.yml` lists job UUIDs this repository has withdrawn, and **Reimport Jobs**
deletes each one after importing the current set. Import is otherwise additive — a job that
stops being generated survives in Rundeck's database as a clickable orphan in a group
nothing else occupies. Only ever list a UUID this repository issued: deleting a job takes
its execution history with it.

The deletion runs from the **new** job definition, so the reorganization that introduced it
needs two Reimport runs — the first imports the definition that can delete, the second
deletes. A UUID that is already gone is reported as such, not as a failure.

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

This makes execution behavior a shared implementation and lets the next job run use a fix
that has reached the tracked branch.

Escape hatches, per job or per shell: `LAB_REFRESH=0` runs the on-disk checkout unchanged
(for pinning while debugging), `LAB_DOCTOR=0` skips the config check (the diagnostic
configuration jobs set this themselves — they exist to diagnose and fix a config the
doctor is complaining about).

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

The full `/keys` tree and Rundeck's project configuration use Rundeck 6.0's AES-256-GCM
converter. Its password is supplied through the root-owned systemd EnvironmentFile
`/etc/rundeck/.storage-password` (`root:rundeck`, `0440`), and the generated handover copy
must be backed up separately. Both converter namespaces use that one recovery value.
Encryption protects stored values; root on a running runner can still access the converter
key and job memory.

The pre-cutover `keys/proxmox/api-token`, `keys/rundeck/homelab-ssh`, and Cloudflare
bootstrap entries are imported into their canonical Vaultwarden items, verified, then
deleted. Recovery is documented in [VAULTWARDEN-RECOVERY.md](VAULTWARDEN-RECOVERY.md).

When the control plane itself is lost, use [RUNNER-RECOVERY.md](RUNNER-RECOVERY.md) for
the dependency-ordered procedure. It starts with independently available PBS access and
decryption material, restores a stopped/isolated runner, restores both converter
namespaces before ordinary jobs, and only then recovers Vaultwarden and its dependent
services. It does not authorize cutover, credential rotation, source shutdown, or cleanup.

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

## Reading and changing config from the UI

`config/` lives on the runner. The supported UI operations read and change it without an
interactive SSH session:

- **Configure App** writes `config/apps/<instance>.yml` from a form. Fields are overrides;
  blank keeps the current value. `extra_yaml` covers anything the form does not name.
- **Get Config** reads the set back out, redacted, and writes an unredacted `tar.gz`
  restore point under `artifacts/` on the runner. Rundeck OSS serves no artifacts, so fetch
  that file over scp if you want it off the host — or rely on PBS, which backs up the runner
  along with it.
- **Store Secret** writes one field of one canonical Vaultwarden item from a form, with
  the value as a secure option — masked in the form, masked in the log, passed to Ansible
  through the environment rather than argv, and stored as a hidden field. It is the only
  post-cutover route into the vault: **Vaultwarden Cutover** exports `LAB_SEED_MODE=1` and
  `lab-run.sh` refuses Seed mode once the vault-mode marker exists, so a credential
  authored later cannot go through the importer.
- **Config Doctor** validates everything and names each problem by file and key path.

Every write first copies the current file to `<dir>/.backups/<file>.<timestamp>`, prunes
that file's backups to the newest 20, and writes the diff to the job log. This is
point-in-time recovery, not a commit history.

**Recover / Drills / Prove Recovery** owns the bounded PBS guest dispatcher. Its default
plan is read-only; execution requires explicit disposable whole-guest authority and every
workload acknowledgement. Existing and stopped/isolated new destinations use Restore Guest;
new activation/data checks remain pending. Serving-only results cannot claim durable data.
Artifact retention and failed-target retry instructions live in the job description. This
job is not required for ordinary install/function/convergence checks.

### Decommission handoff

**Manage / Lab / Maintenance / Decommission Lab Preflight** projects the repository-owned
[`decommission.yml`](../ansible/playbooks/maintenance/decommission.yml) plan/unwire route.
Its description contains the recovery/export prerequisites, literal confirmation,
provider availability requirements and precise outside-runner authority transfer.
Final execution is never a runner job: independent node root supervises the runner's last
destruction and subsequent credential withdrawal. Existing unstamped/adopted/shared
objects and independent datastores/artifacts remain outside automatic erasure authority.
The [maintenance guide](../ansible/playbooks/maintenance/README.md#whole-lab-decommission)
documents the private request/plan/journal formats, exact node helper options and partial
retry. Reimport Jobs is needed to project this definition after the implementation is
available to `LAB_BRANCH`; implementation itself performs no import or lab operation.
