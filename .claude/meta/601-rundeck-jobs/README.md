# 601 — Rundeck job definitions

**Status:** built — all 15 jobs imported and visible on the live Rundeck (2026-07-26); one of fifteen has run. Decisions and deviations from the approach below are in notes.md.
**Depends on:** 500 (bootstrap), and ideally most app slices
**Blocks:** the "import and click" Rundeck experience

## Problem

`rundeck/README.md` references `rundeck/jobs/*.yaml` but no files exist. CLAUDE.md promises importable definitions.

## Files

To create:
- `rundeck/jobs/bootstrap.yaml`
- `rundeck/jobs/deploy-<app>.yaml` (one per app)
- `rundeck/jobs/remove-app.yaml`
- `rundeck/jobs/wire-<stack>.yaml`
- `rundeck/jobs/rollback-container.yaml`
- `rundeck/jobs/lab-status.yaml`
- `rundeck/jobs/check-native-updates.yaml`
- `rundeck/jobs/restart-app.yaml`
- `rundeck/jobs/tail-applog.yaml`

## Approach

Rundeck job YAML format (`rd jobs load --file <file>`). Each job has:
- `name`, `description`, `uuid` (stable per job, so re-import doesn't dupe)
- `options` — survey-equivalent (e.g. `instance` as a required string)
- `sequence` — single step calling Ansible Playbook plugin with the path
- `nodefilters` if needed (most run on localhost via the Ansible plugin)
- Key references: `keys/proxmox/api-token`, `keys/vaultwarden/admin-token`

Same dispatch decision as 600: one job per app vs one parameterized "Deploy App" job. Recommend one-per-app for the one-click UX.

Schedule for `check-native-updates.yaml`: weekly cron.

## Acceptance

- [x] All files load via `rd jobs load --file rundeck/jobs/*.yaml` — 15/15 imported clean
      2026-07-26 (via the equivalent REST import; `rd` is not installed)
- [x] Jobs are visible in Rundeck UI — under Bootstrap / Apps / Maintenance as designed
- [ ] Each job runs to completion against a populated config — **1 of 15**. Only Lab
      Status has run (green). The other fourteen all mutate the lab and are unobserved.
- [x] check-native-updates is scheduled and running on cron — registered SCHEDULED;
      the first firing has not been watched
