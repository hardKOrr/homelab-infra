# 600 — Semaphore project.json

**Status:** open
**Depends on:** 500 (bootstrap), and ideally most app slices so the deploy templates have real targets
**Blocks:** the "import and click" Semaphore experience

## Problem

`semaphore/README.md` references `project.json` but the file does not exist. CLAUDE.md promises an importable project.

## Files

- `semaphore/project.json` — create

## Approach

Semaphore exposes a project export/import via its API. Build the JSON by hand or by exporting from a working Semaphore instance once jobs are configured.

Contents:
- Project metadata (name: homelab-infra, alert email)
- Repository pointing at this git repo, branch: ansible (or master once merged)
- Inventory: dynamic (community.proxmox plugin requires the playbook to import vars first — confirm Semaphore can pass `-e @user-vars.yml`)
- Environment: PROXMOX_API_TOKEN, VAULTWARDEN_ADMIN_TOKEN as secrets
- Templates (one per job from semaphore/README.md table):
  - Bootstrap Platform → bootstrap.yml
  - Deploy App → apps/{{ APP }}.yml with `instance` survey variable
  - Remove App → apps/remove.yml with `instance` survey variable
  - Wire Stack → stacks/wire-{{ stack }}.yml with `stack` survey
  - Rollback Container → stacks/rollback-container.yml with three surveys
  - Lab Status → maintenance/status.yml
  - Check Native Updates → maintenance/check-native-updates.yml (cron: weekly)
  - Restart App → maintenance/restart-app.yml with `instance` survey
  - Tail App Log → maintenance/tail-applog.yml with `instance` + `lines` surveys

Decision point: how does Deploy App know which app's playbook to run? Options:
- (A) one template per app (lots of templates, simplest UI)
- (B) one Deploy template with `app` survey that selects the playbook by interpolation: `playbooks/apps/{{ app }}.yml`
- (C) the survey populates both `app` and `instance`, and a wrapper playbook dispatches

Pick A (one-click promise — user shouldn't have to type the app name). Generate the templates programmatically when adding a new app (slice scaffolding could include a Semaphore template snippet).

## Acceptance

- [ ] `project.json` imports cleanly into a fresh Semaphore install
- [ ] All listed jobs appear in the UI
- [ ] Each job runs successfully against a populated config
- [ ] Surveys validate (no free-text where dropdowns are appropriate)
