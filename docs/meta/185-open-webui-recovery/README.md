# 185 — Open WebUI recovery methods and both restore destinations

**Status:** built
**Subject:** Open WebUI application recovery
**Related:** historical #74 (recovery program), #76 (inventory owner and must-keep order), #77 (method dispositions), #78 (destination orchestration), #89 (shared guest recovery), #149 (Open WebUI named upstreams); current observation issue #225 (live acceptance)

## Goal

Document Open WebUI's supported native `data.pxar` recovery unit, whole-`ai`-guest fallback, unsupported project-managed/rebuild-only dispositions, generated-key and named-upstream dependencies, and both restore destinations. Manual and recurring backup now share one guarded helper, and existing-target recovery verifies independent B before replacing data. Repository fixtures are closure evidence; live schedule, artifact, installed version, credential, external dependency and application validation remains with observation issue #225.

## Remaining

- [x] Record the method dispositions, exact state boundary, version-label caveat, credentials, external data, shared-guest effects and exclusions.
- [x] Use one non-pruning manual/recurring `data.pxar` helper; check SQLite consistency and retain the generated application key securely for a new target.
- [x] Verify an independent existing-target B point before replacement; cover isolated new restore, A → B → restore A, source isolation, failure and retry in fixtures.
- [x] Run the repository lint and test gates before publishing this slice.
- [ ] Live acceptance remains deferred to observation issue #225: observe the schedule, full PBS identity/age/integrity, installed source version/digest, key/upstream dependencies, consistency, and both authorized destination results. Do not infer health from a fixture or perform cutover/cleanup.

## Links

- `docs/specs/open-webui-recovery.md` — normative product disposition, recovery unit, prerequisites, destinations and failure protocol; linked to #225 evidence.
- `notes.md` — safe repository fixture evidence; live verification remains explicitly false.
- `ansible/roles/open-webui/tasks/main.yml` — recurring timer and root-only recovery configuration.
- `ansible/roles/open-webui/files/open-webui-recovery` — shared manual/scheduled capture and SQLite consistency check.
- `ansible/roles/open-webui/tasks/backup.yml`, `tasks/restore.yml` — application backup/restore adapters and key/B-point verification.
- `ansible/playbooks/maintenance/restore-app.yml` — shared method/destination dispatch and protected source-key transfer.
- `gate/test-open-webui-recovery.py`, `gate/test-open-webui-contract.sh` — product-specific fixtures and implementation contract.
- `gate/test-recovery-acceptance.py`, `gate/test-recovery-coverage.py` — shared two-destination matrix and evidence rollup.
