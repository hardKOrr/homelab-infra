# 600 — Semaphore project.json

**Status:** built
**Subject:** Runners / UI
**Related:** 601 (the Rundeck equivalent, which the live lab runs), 500

## Goal

`semaphore/README.md` referenced a `project.json` that did not exist. The file now carries
project metadata, the repository and branch, the inventory, the environment secrets, and one
template per job.

**One template per app, not a parameterized "Deploy App"** — the same dispatch decision as
601, taken for the one-click promise: an operator should not have to type an app name.
`Remove App`, `Restart App`, `Tail App Log` and `Rollback Container` keep their surveys.

**Carried caveat:** the backup schema is *reconstructed*, not exported from a running
Semaphore. If the restore rejects it, dump `GET /api/project/<id>/backup` and commit the
server's output.

Semaphore also gets no `lab-run.sh` and no `bootstrap-semaphore.sh` — it re-clones the repo
before every task, so the checkout is current by construction, and its remaining manual
steps are documented as manual rather than implied to have parity.

## Remaining

Live acceptance needs a restore into a fresh Semaphore.

- [ ] `project.json` imports cleanly into a fresh Semaphore install
- [ ] All listed jobs appear in the UI
- [ ] Each job runs successfully against a populated config
- [ ] Surveys validate — no free text where a dropdown belongs

## Links

- `semaphore/project.json`, `semaphore/README.md`
- [notes.md](notes.md) — decisions and deviations
