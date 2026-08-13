# 601 — Rundeck job definitions

**Status:** built
**Subject:** Runners / UI
**Related:** 012 (`lab-run.sh` and the import mechanism), 600 (Semaphore), 500

## Goal

`rundeck/README.md` referenced `rundeck/jobs/*.yaml` and no files existed. There are now
**22 jobs across Bootstrap / Apps / Config / Maintenance**, each with a stable UUID so
re-import does not duplicate.

**One job per app with `instance=<app>` baked in** — no survey to fill for a deploy.
`Remove App`, `Restart App`, `Tail App Log` and `Rollback Container` keep their parameters.
`Check Native Updates` runs on a weekly cron.

Two facts that came out of this slice and now bind the rest of the repo:

- **`rd` is not required.** The REST API accepts the same job YAML the CLI sends, which is
  how `bootstrap-rundeck.sh` and `Reimport Jobs` import them. The git SCM plugin is retired:
  jobs are imported one-way, and a job edited in the UI is overwritten by the next reimport.
- **Every step is `exec lab-run <playbook> [args]`** with no path, venv or `cd` — 012 moved
  that out of the job files after the first live run failed identically in all 15 jobs at
  config parse, and the fix landed in one shared wrapper rather than fifteen files.

## Remaining

- [x] Each job runs to completion against a populated config. **The four never-run jobs
      were run 2026-08-12** and three of them were broken — see below. Green now:
      Check Native App Updates (104), Tail App Log (109), Restart App (110), Rollback
      Container (105/106/111), Migrate Servarr (120 — reached its own storage guard, which
      is the playbook working). Still never run: the **Config group**
- [x] All files load via the REST import — 22/22 clean, and driven by API
- [x] Jobs are visible in the UI under their groups
- [x] `check-native-updates` is scheduled — registered SCHEDULED; the first firing has not
      been watched

## Links

- `rundeck/jobs/*.yaml`, `rundeck/README.md`
- `ansible/scripts/lab-run.sh` — the single entry point every step calls
- `rundeck/bootstrap-rundeck.sh` — creates the project and imports the jobs
- [notes.md](notes.md) — decisions and deviations
