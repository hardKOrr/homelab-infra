# 202 — Implement configure-pbs

**Status:** done — all six acceptance items observed. Five landed in the green bootstrap of 2026-08-03; the sixth was confirmed on 2026-08-08, when the nightly job proved to have been completing all along. Closing it also cost one defect: the job was backing up the PBS VM into its own datastore, which fails every night (fixed in `8d31ba4`).
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
- [x] The backup job produces a snapshot — **observed 2026-08-08, and no manual
      trigger was needed.** `pvesm list pbs-homelab` returns 30 snapshots: five
      consecutive nightly runs (2026-08-04 through 08-08) for each of the six LXC
      guests, 0.9–3.1 GB each. The backup story is proved end to end, on a schedule,
      unattended. The 2026-08-03 reading of "zero snapshots" was taken hours after the
      job was created and before its first 02:00 fire
- [x] Notifications land in Ntfy — `PBS backups configured` at 15:50:03,
      "Datastore 'homelab' ready; backup job covers 7 guest(s)"
- [x] `config/.generated/facts.yml` has the block with endpoint — under `backups:`

### Resolved: PBS was backing itself up, and it failed every night

The 2026-08-03 run raised this as a question — the vmid list included **168000015, PBS
itself**, and a PBS VM whose only backup lives inside its own datastore is not
recoverable from it. The 2026-08-08 evidence settles it as a defect rather than a
judgement call: the guest has **zero snapshots while the other six have five each**, and
the nightly task log ends

```
INFO: Starting Backup of VM 168000015 (qemu)
ERROR: Backup of VM 168000015 failed - VM 168000015 qmp command 'backup' failed -
       backup connect failed: command error: http upgrade request timed out
INFO: Backup job finished with errors
```

QEMU cannot stream a backup into the datastore hosted by the VM being snapshotted, so
the attempt hangs until it times out and **fails the whole nightly job** — the job has
reported errors every night since it was created, which is also why the criterion above
looked unmet. `configure-pbs.yml` now rejects guests carrying the tag of the PBS
instance being configured, so a second PBS under a different name is still covered.

The template question is answered: template 9001 is excluded by the explicit
`rejectattr('template', 'equalto', 1)` already in that task, not by anything implicit.
