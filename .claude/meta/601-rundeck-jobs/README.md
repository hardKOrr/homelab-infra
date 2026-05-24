# 601 — Rundeck job definitions

**Status:** open
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

- [ ] All files load via `rd jobs load --file rundeck/jobs/*.yaml`
- [ ] Jobs are visible in Rundeck UI
- [ ] Each job runs to completion against a populated config
- [ ] check-native-updates is scheduled and running on cron
