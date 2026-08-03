# 202 — Implement configure-pbs

**Status:** built — ran live 2026-08-03 in the green bootstrap; five of six acceptance items observed. The sixth needs a backup job actually triggered: the datastore holds zero snapshots, so nothing has yet proved a backup completes end to end.
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

Observed 2026-08-03 unless noted.

- [x] Datastore exists on PBS — `homelab`, reachable from PVE, 62 GB available
- [x] PVE node registered as source — storage `pbs-homelab`, type `pbs`,
      server `192.168.0.15`, `active=1`
- [x] Backup job scheduled — job `08ee3016-…` at 02:00 to `pbs-homelab`, carrying the
      explicit vmid list of all seven tagged guests. Recorded as met on the PVE side; the
      criterion said "visible in PBS UI", but vzdump jobs live on PVE, which is where the
      implementation correctly puts it
- [ ] Manual trigger of the backup job produces a snapshot — **not observed.** The
      datastore contains zero items. Nothing has proved a backup actually completes,
      which is the only criterion that tests the backup story rather than its wiring
- [x] Notifications land in Ntfy — `PBS backups configured` at 15:50:03,
      "Datastore 'homelab' ready; backup job covers 7 guest(s)"
- [x] `config/.generated/facts.yml` has the block with endpoint — under `backups:`

### Open question raised by the live run

The vmid list includes **168000015, PBS itself**, because PBS is tagged `homelab-infra`
like every other guest this platform creates. A PBS VM whose only backup lives inside its
own datastore is not recoverable from that backup. Worth deciding whether the job should
exclude the backup server. Separately, template 9001 is tagged `homelab-infra` but is not
in the list, so the tag-to-vmid expansion is already filtering something — confirm what.
