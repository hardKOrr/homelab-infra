# 406 — PBS role + playbook

**Status:** done — deployed live 2026-08-03 in the green vault-mode bootstrap (Rundeck execution 12, revision bb84574), `pbs` ok=72 changed=23 failed=0. All four acceptance items observed; evidence below. Decisions and deviations from the approach below are in notes.md.
**Depends on:** 202 (configure-pbs), 401 (ntfy)
**Blocks:** backup story

## Problem

Bootstrap step 7 is PBS. No role or playbook exists. PBS is a full VM with its own installer ISO — not LXC, not Docker.

## Files

To create:
- `ansible/roles/pbs/{tasks,handlers,defaults,meta}/...`
- `ansible/playbooks/apps/pbs.yml` (uses PATH B-equivalent for VM provisioning via `vm-create.yml`)
- `ansible/vars/app-defaults/pbs.yml`
- `config.example/apps/pbs.example.yml`

## Approach

Provisioning challenge: PBS ships as an ISO installer. Options:

**A** — Use a community PBS cloud-init template (e.g. Helper Scripts community-scripts). Fast, but introduces an external dependency.

**B** — Install Proxmox Backup Server packages on a Debian base — `apt install proxmox-backup-server` from the PBS apt repo. Cleaner control, slightly more steps.

Pick B. Steps:
1. Provision Debian VM via `vm-create.yml`.
2. Run guest-bootstrap.
3. Add PBS apt repo + key.
4. Install `proxmox-backup-server`.
5. Bootstrap admin user, generate API token, store in Vaultwarden.
6. Hand off to slice 202 (`configure-pbs.yml`) for datastore + job setup.
7. Wire Caddy + Authentik + Uptime Kuma + DNS (the PBS web UI).

facts:
```yaml
pbs:
  api_url: https://pbs.<domain>:8007
  api_token_id: ...
  api_token_secret: <from-vault>
```

## Acceptance

All four observed 2026-08-03 against the live lab.

- [x] PBS VM created, web UI loads on port 8007 — VM 168000015 `pbs`, running,
      tagged `homelab-infra;pbs`; `https://192.168.0.15:8007/` returns 200
- [x] API token works (curl test) — proved by use rather than by curl: the PVE storage
      `pbs-homelab` authenticates with `root@pam!ansible` and reports
      `active=1`, 62 GB available
- [x] facts.yml has pbs block — written under the registry's `backups:` key
      (`host: https://192.168.0.15:8007`, `datastore: homelab`,
      `api_token_id: root@pam!ansible`); the secret itself is in Vaultwarden, not here
- [x] Slice 202 can run successfully after this — it ran in the same bootstrap and
      notified `PBS backups configured` at 15:50:03

### VM provisioning machinery — first live execution

The INDEX called `ensure-cloud-template.yml` + `vm-clone.yml` the highest live-run risk in
the set, and this was their first execution. Both worked, after one fix (`bb84574`): the
template was discovered by name and tag and adopted at vmid 9001, where a previous build had
put it, instead of being demanded at the configured 9000 — which on this lab holds the
operator's own hand-built `debian12-cloudinit`. Build steps correctly skipped; PBS cloned
from the adopted template.
