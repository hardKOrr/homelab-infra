# 202 — Implement configure-pbs

**Status:** open
**Depends on:** 200, 406 (PBS VM must exist)
**Blocks:** backup story for the platform

## Problem

`tasks/bootstrap/configure-pbs.yml` is a TODO header only. Backups are claimed as a day-2 feature in CLAUDE.md but nothing configures them.

## Files

- `ansible/tasks/bootstrap/configure-pbs.yml` — implement
- (consumes) `homelabinfra_config.infrastructure.backups.*` from `config/infrastructure.yml`

## Approach

Run via API against the PBS VM on port 8007 (PBS uses its own REST API — not the PVE API). Steps:

1. Authenticate to PBS — token-based, token created during PBS VM provisioning and stashed in Vaultwarden.
2. Ensure datastore exists at `backups.datastore_path`.
3. Add the Proxmox PVE node as a remote/source.
4. Create a backup job covering all guests tagged `homelab-infra` with the configured schedule and retention.
5. Trigger a test notification via PBS's own notification system (PBS has native notification targets — point one at Ntfy).
6. Call `write-generated-facts` to record PBS endpoint + dataset name.

PBS API docs: https://pbs.proxmox.com/docs/api-viewer/index.html

## Acceptance

- [ ] Datastore exists on PBS
- [ ] PVE node registered as source
- [ ] Backup job scheduled, visible in PBS UI
- [ ] Manual trigger of the backup job produces a snapshot
- [ ] Notifications land in Ntfy
- [ ] `config/.generated/facts.yml` has `pbs:` block with endpoint
