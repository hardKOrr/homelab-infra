# 602 — Rundeck job tree

**Status:** built
**Subject:** Rundeck job tree
**Related:** 601 (the job definitions this reshapes), 408 (the application catalog that
feeds the selectable set), 600 (Semaphore, no-target — not kept in parity)

## Goal

Give the 39-job Rundeck surface a catalog-first hierarchy that remains usable as the
application set grows. Deployable applications are found by human purpose, application
type, and name under `Applications/`; platform capabilities, routine management, recovery,
and setup have truthful separate roots. `catalog/applications.yml` owns application
classification independently of hosting kind, and `rundeck/job-groups.yml` owns every
other placement. The renderer projects and validates both classifications before bootstrap
or reimport. Job names, UUIDs, steps, options, schedules, and playbooks are unchanged.

## Remaining

- [x] built 2026-08-23 — all 39 source jobs moved from the four flat groups into the
      nested `Applications`, `Platform`, `Manage`, `Recover`, and `Setup` tree.
- [x] built 2026-08-23 — application purpose/type has one machine-readable home and the
      app-author path requires a catalog entry.
- [x] built 2026-08-23 — bootstrap and **Reimport Jobs** reject incomplete classification
      or a stale projected `group:` before importing any job.
- [x] built 2026-08-23 — `AGENTS.md`, `rundeck/README.md`, and the architecture map describe
      the same tree and ownership boundary.
- [ ] Run **Reimport Jobs** against the live project and confirm `39 succeeded, 0 failed`,
      no duplicate UUID, the weekly update schedule preserved, and no old flat group left.

## Links

- `catalog/applications.yml` — canonical purpose/type classification for selectable apps
- `rundeck/job-groups.yml` — complete classification for platform and operator jobs
- `rundeck/render-job.py` — projection and pre-import validation
- `rundeck/jobs/*.yaml` — reviewable projected groups on the stable job definitions
- `gate/test-rundeck-job-tree.sh` — complete-tree and render regression check
- `AGENTS.md` — authoritative operator-facing tree
- notes.md — dated design history and superseded alternatives
