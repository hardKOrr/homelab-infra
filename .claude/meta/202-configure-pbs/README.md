# 202 — Implement configure-pbs

**Status:** built (implementation complete; live acceptance blocked on 406)
**Depends on:** 200, 406 (PBS VM must exist)
**Blocks:** backup story for the platform

## Problem

`tasks/bootstrap/configure-pbs.yml` is a TODO header only. Backups are claimed as a day-2 feature in CLAUDE.md but nothing configures them.

## Files

- `ansible/tasks/bootstrap/configure-pbs.yml` — implement
- (consumes) `homelabinfra_config.infrastructure.backups.*` from `config/infrastructure.yml`

## Approach

Run via API against the PBS VM on port 8007 (PBS uses its own REST API — not the PVE API).

**Corrected during implementation:** PBS "remotes" are other PBS instances (sync sources) — PBS
cannot pull guest backups from a PVE node. Backups flow the other way: PVE pushes via vzdump to
a PBS-type storage. Steps as implemented:

1. Authenticate to PBS — token-based, token created during PBS VM provisioning and stashed in Vaultwarden.
2. Ensure datastore exists at `backups.datastore_path`; apply `keep-*` retention on it.
3. PBS notifications → Ntfy: webhook notification target + matcher routing all notifications.
4. PVE side: register PBS as a `pbs`-type storage (with PBS cert fingerprint), then create a
   vzdump backup job with the explicit vmid list of `homelab-infra`-tagged guests (PVE jobs
   cannot select by tag; re-running refreshes the list) using the configured schedule.
5. Send a configure-complete test notification to Ntfy directly.
6. Call `write-generated-facts` to record PBS endpoint + datastore under `backups:`.

PBS API docs: https://pbs.proxmox.com/docs/api-viewer/index.html

## Acceptance

- [ ] Datastore exists on PBS
- [ ] PVE node registered as source
- [ ] Backup job scheduled, visible in PBS UI
- [ ] Manual trigger of the backup job produces a snapshot
- [ ] Notifications land in Ntfy
- [ ] `config/.generated/facts.yml` has `pbs:` block with endpoint
