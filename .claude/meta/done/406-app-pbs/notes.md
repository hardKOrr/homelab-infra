# 406 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items. **This slice added new VM provisioning
machinery that has never been exercised — see "Risk" below.**

### Decision: install PBS packages on a Debian VM (README option B), and build the
### template automatically

The README picked option B (apt install on Debian) over a community cloud-init
template, and this implementation keeps that. But option B assumed a Debian VM could
simply be created, and it could not: `tasks/proxmox/vm-create.yml` builds a VM with
**no operating system**. Proxmox has no VM equivalent of an LXC `ostemplate`, and
nothing in the repo imported a cloud image.

Two ways to close that were put to the user: require a hand-made template named in
config, or build the template automatically. **Automatic** was chosen — requiring a
manual Proxmox step before bootstrap can finish step 7 would break the one-click
promise for exactly the audience this project is shared with.

### New, reusable beyond PBS

- `tasks/proxmox/ensure-cloud-template.yml` — downloads the Debian genericcloud
  qcow2 on the Proxmox node, `qm create` / `qm set --scsi0 …import-from=` /
  cloud-init drive / `qm template`. Idempotent: a no-op when the template vmid
  already exists, and it never overwrites one. Deleting the template VM forces a
  rebuild. Any future VM app clones the same template.
- `tasks/proxmox/vm-clone.yml` — clone → apply per-VM config → resize → start → wait
  for the guest agent → resolve the address.

`vm-clone.yml` is a **separate file from `vm-create.yml`, deliberately**. When
`proxmox_kvm` clones, `vmid` means the SOURCE template and `newid` the new VM, and
the clone call ignores most configuration parameters — the opposite of
`vm-create.yml`'s "one call, vmid is the target, all params applied" shape. Folding
the two together made both harder to read than keeping the paths separate. Slice
003's `vm_module_keys` allowlist is untouched as a result.

### Decision: API token, not a password

The role creates `root@pam!ansible` with `proxmox-backup-manager`, grants it Admin at
`/`, and hands the secret back for `tasks/bootstrap/configure-pbs.yml` (slice 202) to
authenticate with. PBS reveals a token secret exactly once, at creation, so:

- the recorded secret is **verified** against the live API before being reused;
- a token that exists but cannot be verified is deleted and recreated, because an
  unverifiable secret is useless to us;
- rotation is reported explicitly, since anything else configured with the old secret
  needs updating.

Without the verify step, a restored VM or a hand-deleted token would surface as an
opaque 401 inside configure-pbs, far from the cause.

Contract addition: `backups` gains provider-specific optional `api_token_id` /
`api_token_secret` (CONTRACT.md §3). The playbook writes them *before* calling
configure-pbs — that task authenticates with them, and recording them first is what
lets the next deploy reuse the token instead of rotating it. `write-generated-facts`
deep-merges, so configure-pbs's own `backups` write preserves them.

### Handoff to slice 202

Play 3 calls `tasks/bootstrap/configure-pbs.yml` directly rather than leaving it to
bootstrap.yml, so a standalone `apps/pbs.yml` run produces a fully configured backup
target. That task creates the datastore, applies retention, routes notifications to
Ntfy, registers PBS as a PVE storage backend and creates the vzdump job.

PBS is imported **last** in bootstrap for a reason its own task documents: the backup
job is built from the vmid list of every homelab-infra-tagged guest existing at
configure time.

### Risk — read before the first live run

None of the VM path has ever run:

- `vm-create.yml` was already untouched by any working playbook (`homelabinfra-defaults.yml`
  still carries `vm: #TODO: EVerything about VM stuff`).
- `ensure-cloud-template.yml` and `vm-clone.yml` are new this slice.

Specific assumptions to check first:

- `qm set <id> --scsi0 <storage>:0,import-from=<path>` — PVE 8+ syntax. Fine for the
  PVE 9.1 this project targets, but it is the step most likely to need adjusting on
  an older node.
- `local-lvm` as the default template/clone storage; labs using ZFS or a different
  storage id must override `proxmox.cloud_template.storage` and `proxmox.vm.storage`.
- The Proxmox node must be reachable as an Ansible host (these tasks `delegate_to` it),
  same requirement `lxc-create.yml` already has.
- `proxmox_kvm`'s `update: true` applying cloud-init and network settings to a clone.

### Verification

- ansible-lint: clean (production profile).
- syntax-check: `playbooks/apps/pbs.yml` and `playbooks/bootstrap.yml` clean.
  Repo-wide, only the pre-existing slice-502 stub fails.
- NOT verified live. Nothing here has touched a Proxmox node.

### Live acceptance TODO

- Cloud template builds unattended and is reusable (second VM app clones it).
- PBS VM created, web UI loads on 8007.
- API token works (`curl` with `PBSAPIToken=`), and re-run does NOT rotate it.
- Slice 202 (`configure-pbs.yml`) completes: datastore, retention, Ntfy target, PVE
  storage backend, vzdump job covering the tagged guests.
- A backup actually runs on schedule and reports to Ntfy.
