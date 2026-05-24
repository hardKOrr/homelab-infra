# 406 — PBS role + playbook

**Status:** open
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

- [ ] PBS VM created, web UI loads on port 8007
- [ ] API token works (curl test)
- [ ] facts.yml has pbs block
- [ ] Slice 202 can run successfully after this
