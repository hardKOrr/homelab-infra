# 012 — Runner onboarding: make the handover executable, not a checklist

**Status:** open
**Depends on:** 010 (config provenance) — shares the secrets-and-Key-Storage edit surface
**Blocks:** any honest claim that this repo is shareable

## Problem

The project's stated promise is *clone, fill in two config files, run bootstrap*. The
delivered path is fourteen manual steps spread across three documents, one of which
(refreshing the checkout) is written down nowhere at all.

| # | Step | Documented at |
|---|---|---|
| 1 | Copy `RUNDECK_API_TOKEN` from `/root/.rundeck-bootstrap` into `.env` | `bootstrap-rundeck.sh:362` |
| 2 | Create the `homelab-infra` Rundeck project | `bootstrap-rundeck.sh:365` |
| 3 | Configure the git SCM import plugin | `bootstrap-rundeck.sh:366` |
| 4 | Stage Key Storage: `keys/proxmox/api-token`, `keys/vaultwarden/admin-token` | `bootstrap-rundeck.sh:367` |
| 5 | Run the `rd jobs load` loop over all 16 job files | `rundeck/README.md:38` |
| 6 | Hand-write `config/proxmox.yml` on the runner | `rundeck/README.md:82` |
| 7 | Hand-write `config/infrastructure.yml` on the runner | `rundeck/README.md:82` |
| 8 | Stage `keys/rundeck/homelab-ssh` | `rundeck/README.md:87` |
| 9 | Paste the Vaultwarden admin token in after bootstrap step 1 | `CLAUDE.md`, Secrets |
| 10 | Edit `REPO`/`VENV` in all 16 job files if paths differ | `rundeck/README.md:69` |
| 11 | **`git pull` the runner's checkout — nothing does it** | *nowhere* |
| 12 | Fix the repository URL in the restored Semaphore project | `semaphore/README.md:15` |
| 13 | Paste the SSH key into Semaphore | `semaphore/README.md:18` |
| 14 | Fill the `PROXMOX_API_*` env vars in the `Homelab` environment | `semaphore/README.md:19` |

**There is no README at the repository root.** The root holds four directories and
nothing that tells a person who cloned it what to do. Every path above is discoverable
only by reading a script header or a subdirectory README.

Three of these are not chores — they are gaps in the design:

- **#11 has no owner.** The repo reaches the runner exactly once, via the clone at
  `bootstrap-rundeck.sh:270-280`. Every job step then `cd`s into that path and runs
  whatever is on disk. The only refresh path is re-running the whole bootstrap script.
  A fix pushed to `master` does not reach the platform that runs it.
- **#3 does not solve #11 and never could.** Rundeck's git SCM import syncs *job
  definitions*, not the working tree the script steps execute. The two are separate
  problems and the docs conflate them. Retire the SCM line or scope it explicitly to
  jobs.
- **#10 exists because 16 job files each redeclare `REPO` and `VENV`.** The same
  duplication already cost a live outage: slice 601's first run failed identically in
  all 15 jobs, and the fix landed in the one shared wrapper rather than 15 files
  (`with-proxmox-env.sh`, commit `059316a`). The lesson generalises and has not been
  applied to the rest of the step body.

Steps 1, 4, 6, 7, 8, 9, 13 and 14 are all the same underlying defect as slice 010 —
credentials with no provenance, hand-placed on one host. **010 and 012 should land
together**; 010 decides where a secret lives, 012 decides who puts it there.

## Files

- `ansible/scripts/lab-run.sh` (new) — the single entry point every job step calls.
  Refreshes the checkout, resolves `REPO`/`VENV`, delegates to `with-proxmox-env.sh`,
  execs `ansible-playbook`. **Ships in the repo and arrives via the clone** — it is not
  installed onto the host by hand.
- `rundeck/jobs/*.yaml` (16) — each script step collapses to one `lab-run.sh` call.
  Deletes 16 copies of `REPO`/`VENV` and closes #10.
- `semaphore/project.json` — same collapse for the Semaphore steps.
- `rundeck/bootstrap-rundeck.sh` — absorb #2, #4, #5: create the project over the REST
  API, stage Key Storage from the environment, import `rundeck/jobs/*.yaml`. The API
  accepts the same YAML the CLI sends (`POST /api/47/project/<p>/jobs/import`), so no
  `rd` binary is required. Rewrite the closing summary: it currently lists "author
  `rundeck/jobs/*.yaml`" as outstanding, which slice 601 completed.
- `README.md` (new, repository root) — the entry point. What this is, the two files a
  user fills in, the one command that stands up a runner, and where to go next.
- `rundeck/README.md`, `semaphore/README.md` — reduce to what genuinely cannot be
  automated, and say why for each survivor.

## Approach

- **Every remaining manual step earns its place or dies.** A step survives only if a
  machine cannot perform it (a human pasting a secret it alone holds) or a human must
  decide something. Everything else moves into `bootstrap-rundeck.sh`.
- **One wrapper, not sixteen copies.** `lab-run.sh` holds the path resolution and the
  refresh. Job files carry only the playbook name and its arguments. A change to how
  jobs run is then one edit, as `059316a` already demonstrated.
- **The checkout refreshes itself on every run.** `git fetch` + `reset --hard
  origin/<branch>` before each play, with the resolved commit echoed into the job log so
  every execution records the revision that produced it. `config/` is gitignored and
  untracked, so the reset cannot touch `proxmox.yml`, `infrastructure.yml`,
  `.generated/facts.yml` or `apps/*.yml` — the checkout is git plus local config, and
  that invariant is what makes the reset safe. An env-var opt-out (`LAB_REFRESH=0`)
  covers deliberate pinning while debugging.
- **The bootstrap script finishes the job.** It already logs into Rundeck and issues an
  API token; creating the project, staging Key Storage and importing the jobs use the
  same session. Idempotent, matching the rest of the script.
- **Write the front door.** A root README is the difference between a shareable project
  and a private one that happens to be on GitHub.

## Acceptance

- [ ] `bash rundeck/bootstrap-rundeck.sh` on a bare Proxmox node yields a Rundeck with
      the project created, all 16 jobs imported and Key Storage staged — no UI steps
- [ ] The documented manual steps number **two**: fill `config/proxmox.yml` and
      `config/infrastructure.yml` (or their env-var equivalents per 010)
- [ ] A commit pushed to `master` is executed by the next job run with no human action,
      and the job log names the commit
- [ ] `LAB_REFRESH=0` runs the on-disk checkout unchanged
- [ ] Re-running the bootstrap script against a configured Rundeck changes nothing and
      rotates no credential
- [ ] The repository root has a README that takes a first-time reader from clone to a
      running bootstrap
- [ ] No job file contains a `REPO` or `VENV` assignment

## Open questions

- **Does auto-refresh default on?** On means one click always runs current `master` —
  consistent with fire-and-forget, and it removes a step. Off means deploys are pinned
  to whatever was last pulled and a stale runner is silently possible. Recommended: on,
  with `LAB_REFRESH=0` as the escape hatch. Operator's call.
- **What refreshes a runner whose branch is not `master`?** A fork tracking its own
  branch needs `LAB_BRANCH` set once, on the host, which is a fifteenth manual step
  unless `bootstrap-rundeck.sh` writes it from `REPO_BRANCH` at build time.
- **Does the Rundeck SCM plugin survive at all?** If job definitions sync from git, then
  editing a job in the UI and re-importing from the repo conflict. Either scope it to
  one-way import and say so, or drop it and let `bootstrap-rundeck.sh` re-import be the
  only path.
