# 186 — Plane recovery methods and both destinations

**Status:** built
**Subject:** Plane application-consistent recovery
**Related:** #74 (recovery program), #76 (inventory owner and must-keep order), #77 (method dispositions), #78 (destination orchestration), #80 (acceptance protocol), #89 (shared guest recovery)

## Goal

Implement Plane's native recovery path for the tracked Plane Community `v1.4.2` deployment. Keep the exact recovery unit—named PostgreSQL, named Redis, the complete server/worker and RabbitMQ Compose data tree, and local MinIO object storage—in one PBS point. Support plan/new-isolated and existing-target restore through the shared recovery dispatcher; describe whole-shared-guest recovery only as a fallback, and leave project-managed/rebuild-only unsupported. Repository fixtures and gates are closure evidence here; live schedule, artifact, credential, consistency and application acceptance remains with #80.

## Remaining

- [x] Declare the native method and record `pbs_guest`, `project_managed`, and rebuild-only dispositions without implying application-only guest recovery.
- [x] Add one root-only helper/config and a recurring `0 3 * * *` timer; Backup App calls the same helper. The backup validates the PostgreSQL dump and Redis RDB and uploads all four members as one PBS snapshot, with a non-secret release marker checked before restore, without pruning existing points.
- [x] Restore to a new isolated target without source-host access; require target-owned PostgreSQL/Redis identities and paths, carry required source Plane keys securely, and delay the recurring timer and wiring until verification.
- [x] Restore an existing target only through the common pre-restore-point gate; validate all selected members before mutation, serialize backup/restore work, and leave Plane stopped on a failed replacement for explicit retry.
- [x] Synthetic fixtures cover both destinations, source isolation, A → B → restore A with independently retained B, retry, missing keys/dependencies, corrupt/incomplete artifacts, wrong identity, storage overlap, version incompatibility and shared-guest scope.
- [x] `bash gate/lint.sh` and `bash gate/test.sh` pass for this repository change.
- [ ] Live acceptance remains deferred to #80: observe the schedule, exact PBS identity and age, artifact/member integrity, versions, external dependencies and credentials, then run both authorized isolated and existing-target restore scenarios. No live source or target was changed for this slice.

## Links

- `docs/specs/plane-recovery.md` — method decision, exact unit, dependencies, destinations, failure matrix and live deferral
- `ansible/roles/plane/tasks/backup.yml`, `ansible/roles/plane/files/plane-recovery` — shared recurring/manual capture path
- `ansible/roles/plane/tasks/restore.yml` — Plane-specific restore adapter
- `ansible/playbooks/maintenance/restore-app.yml` — shared method/destination dispatch and isolation
- `gate/test-plane-recovery.py` — Plane-specific synthetic protocol and implementation assertions
- `gate/test-recovery-coverage.py`, `gate/test-recovery-acceptance.py` — version, inventory and shared protocol assertions
