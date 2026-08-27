# 602 — Rundeck job tree

**Status:** done — live organization accepted by the operator 2026-08-26
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

### Phase 2 — one folder per application (2026-08-23)

- [x] built — the leaf of the tree is the application: `…/<App>` holds Deploy, and
      `…/<App>/Maintenance` holds its day-2 jobs. 102 jobs render from 39 source files.
- [x] built — eight per-application templates, `rundeck/app-actions.yml`, and catalog
      schema 2 covering platform services as well as optional applications.
- [x] built — the expansion answers instance, app and stack from the repository; which
      actions exist is derived from the hosting kind and the absences are deliberate.
- [x] built — `instance` is a live dropdown from `/var/lib/rundeck/app-instances/<app>.json`,
      rewritten before and after every job. Multi-estate defaults and labels name every
      estate explicitly as `<app>-<estate>[-<variant>]`.
- [x] built — `Restart` and `Tail Log` now serve Docker apps as well as native ones, through
      `ansible/tasks/maintenance/resolve-app-target.yml`. Without this the expansion would
      have produced ten buttons that fail when pressed.
- [x] corrected — Rollback also resolves the effective instance stack. Authentik and
      Observability expose no Rollback because the generic seam cannot roll their multiple
      images back as one application.
- [x] corrected — a multi-estate map requires one explicit default, and every estate-scoped
      instance uses `<app>-<estate>[-<variant>]`, including the default estate.
- [x] corrected — Deploy jobs use the live instance provider, Configure refreshes it before
      returning, and a failed retired-job deletion makes Reimport fail.
- [x] built — `rundeck/retired-jobs.yml` and the deletion pass in **Reimport Jobs**, so the
      eight retired generic jobs do not survive as orphans.
- [x] built — every routed application declares `routing.subdomain`, so an estate-suffixed
      instance name never reaches a published URL; the gate rejects one that omits it.
- [x] built — `scope` is required in the catalog with no default, and CONTRACT §5 states
      that estates are separate and the shared set is explicitly named.
- [x] built — an application that declares its own `routing.estate` is prefilled for that
      estate rather than for the lab default.
- [x] accepted 2026-08-26 — the operator confirmed the live job tree received its
      organization update. This accepts the slice goal without asserting that each generated
      maintenance action was separately exercised.

### Phase 1 — the catalog-first tree (2026-08-23)

- [x] built 2026-08-23 — all 39 source jobs moved from the four flat groups into the
      nested `Applications`, `Platform`, `Manage`, `Recover`, and `Setup` tree.
- [x] built 2026-08-23 — application purpose/type has one machine-readable home and the
      app-author path requires a catalog entry.
- [x] built 2026-08-23 — bootstrap and **Reimport Jobs** reject incomplete classification
      or a stale projected `group:` before importing any job.
- [x] built 2026-08-23 — `AGENTS.md`, `rundeck/README.md`, and the architecture map describe
      the same tree and ownership boundary.
- [x] observed 2026-08-23 — **Reimport Jobs** execution 280 ran revision `2522869`,
      reported `39 imported, 0 failed`, retained 39 unique UUIDs, left only the five new
      roots, and preserved **Check Native App Updates** at Monday 06:00.

## Links

- `catalog/applications.yml` — canonical purpose/type classification for selectable apps
- `rundeck/job-groups.yml` — complete classification for platform and operator jobs
- `rundeck/render-job.py` — projection and pre-import validation
- `rundeck/jobs/*.yaml` — reviewable projected groups on the stable job definitions
- `gate/test-rundeck-job-tree.sh` — complete-tree and render regression check
- `AGENTS.md` — authoritative operator-facing tree
- notes.md — dated design history and superseded alternatives
