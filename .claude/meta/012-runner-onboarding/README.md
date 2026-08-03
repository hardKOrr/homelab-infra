# 012 — Runner onboarding: make the handover executable, not a checklist

**Status:** built
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

**Ownership note (2026-07-27).** Steps 6, 7 and 9 now belong to other slices and must not be
solved here: 010 makes `bootstrap-rundeck.sh` the *author* of `config/proxmox.yml` and
`config/infrastructure.yml` and the *minter* of the Proxmox token, and 013 removes the
Vaultwarden token paste. This slice keeps the delivery mechanism — `lab-run.sh`, the
checkout refresh, project and job import, the root README — and inherits those three as
already-closed.

**Two bootstrap layers, and they meet here.** `bootstrap-rundeck.sh` on the PVE node builds
the runner and everything the UI needs to exist; `Bootstrap Platform` in the UI builds the
lab. Between them there is no manual step left to justify: the script's last act is a
Rundeck with jobs imported, Key Storage staged and config authored, and the operator's first
act is one click. Every step in the table above lands in one layer or the other — that is
the acceptance bar, not a reduction to some smaller number of chores.

## Files

- `ansible/scripts/lab-run.sh` (new) — the single entry point every job step calls.
  Refreshes the checkout, resolves `REPO`/`VENV`, delegates to `with-proxmox-env.sh`,
  execs `ansible-playbook`. **Ships in the repo and arrives via the clone** — it is not
  installed onto the host by hand.
- `rundeck/jobs/*.yaml` (15 today, 19 after 010 adds Config Doctor, Configure App, Get
  Config and this slice adds Reimport Jobs) — each script step collapses to one
  `lab-run.sh` call. Deletes every copy of `REPO`/`VENV` and closes #10.
- `semaphore/project.json` — same collapse for the Semaphore steps.
- `rundeck/bootstrap-rundeck.sh` — absorb #1, #2, #4, #5: write `RUNDECK_API_TOKEN` into
  `.env` in the checkout it just made rather than telling the operator to copy it, create
  the project over the REST API, stage Key Storage from the environment, import
  `rundeck/jobs/*.yaml`. The API accepts the same YAML the CLI sends
  (`POST /api/47/project/<p>/jobs/import`), so no `rd` binary is required. Rewrite the
  closing summary: it currently lists "author `rundeck/jobs/*.yaml`" as outstanding, which
  slice 601 completed. Shares this file with 010 — one rewrite, not two.
- `README.md` (new, repository root) — the entry point. What this is, the one command on
  a PVE node that stands up the runner, the one click that stands up the lab, and where to
  go next. The two bootstrap layers are the spine of this document.
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
  `.generated/facts.yml`, `apps/*.yml` or `.backups/` — the checkout is git plus local
  config, and that invariant is what makes the reset safe. 010 keeps it that way: config
  is a plain gitignored tree on this host, not a second checkout nested inside it. An
  env-var opt-out (`LAB_REFRESH=0`) covers deliberate pinning while debugging.
- **The bootstrap script finishes the job.** It already logs into Rundeck and issues an
  API token; creating the project, staging Key Storage and importing the jobs use the
  same session. Idempotent, matching the rest of the script.
- **Write the front door.** A root README is the difference between a shareable project
  and a private one that happens to be on GitHub.

## Acceptance

- [x] `bash rundeck/bootstrap-rundeck.sh` on a bare Proxmox node yields a Rundeck with
      the project created, every job imported and Key Storage staged — no UI steps
      — **observed 2026-08-01** on pve-host-3 after destroying the previous runner:
      project created, 19/19 jobs imported, both Key Storage entries staged, config
      authored, and Vaultwarden deployed and serving. Took fifteen fixes to get there;
      see [notes.md](notes.md)
- [x] **The whole onboarding path is one command and one click**: run the script on the
      node, then run `Bootstrap Platform`. Every one of the fourteen steps above is
      performed by one layer or the other, and the root README says so in that order —
      **the click landed green 2026-08-03** (execution 12, all seven services). Recorded
      as met for the path; note that reaching it took a Vaultwarden cutover and three
      failed executions' worth of fixes, so "one click" is true of the finished artifact
      and was not true of the journey
- [ ] Config authored on the runner is editable and readable from the UI alone — no SSH
      session appears anywhere in the documented path (010's Configure App / Get Config)
- [x] A commit pushed to `master` is executed by the next job run with no human action,
      and the job log names the commit — observed repeatedly 2026-08-03; execution 12's
      log opens with `[lab-run] refreshing … to origin/master` then
      `[lab-run] revision bb84574 fix(proxmox): find the cloud template by name and tag,
      not by vmid`, a commit pushed minutes earlier
- [ ] `LAB_REFRESH=0` runs the on-disk checkout unchanged
- [ ] Re-running the bootstrap script against a configured Rundeck changes nothing and
      rotates no credential
- [ ] The repository root has a README that takes a first-time reader from clone to a
      running bootstrap
- [x] No job file contains a `REPO` or `VENV` assignment — verified by grep over
      `rundeck/jobs/`

## Open questions — all resolved by the operator, 2026-07-26

- **~~Does auto-refresh default on?~~** **Yes, on**, with `LAB_REFRESH=0` as the escape
  hatch. But see the build note below: "on" now means *on when a bootstrapped runner says
  so*, not on unconditionally, because the mechanism is a `git reset --hard`.
- **~~What refreshes a runner whose branch is not `master`?~~** **`bootstrap-rundeck.sh`
  writes `LAB_BRANCH` into `/etc/homelab-infra/lab-run.env` from `REPO_BRANCH` at build
  time**, so it is never a manual step. This is also what makes a gitflow-style
  `develop`/`master` split work with no code change: a test runner is
  `REPO_BRANCH=develop bash bootstrap-rundeck.sh` and nothing else moves.
- **~~Does the Rundeck SCM plugin survive at all?~~** **No — dropped.** Job definitions are
  imported one-way from the repo over the REST API, by the bootstrap script and by the new
  `Reimport Jobs` job. Editing a job in the UI is not expected and is not supported: the
  next reimport overwrites it, and the READMEs say so.

## Built — 2026-07-26

Landed together with 010. Both gates green. Live acceptance is unobserved.

### How the fourteen steps landed

| # | Now performed by |
|---|---|
| 1 | `bootstrap-rundeck.sh` writes `.env` into the checkout it made |
| 2 | `bootstrap-rundeck.sh`, `POST /api/47/projects` |
| 3 | **deleted** — SCM plugin dropped; `Reimport Jobs` replaces it |
| 4 | `bootstrap-rundeck.sh` stages Key Storage from values it holds in-process |
| 5 | `bootstrap-rundeck.sh` imports every job over REST from the clone |
| 6, 7 | 010 — the script authors both files |
| 8 | `bootstrap-rundeck.sh` generates the keypair and stages the private half |
| 9 | 013 (open) — until then, one paste survives |
| 10 | **deleted** — no job file names a path |
| 11 | `lab-run.sh` refreshes before every job |
| 12, 13, 14 | still manual on the Semaphore path, and documented as such — there is no `bootstrap-semaphore.sh` |

### Deviations from the plan above, and why

- **`LAB_REFRESH` does not default to 1 unconditionally.** It defaults to 1 only when
  `/etc/homelab-infra/lab-run.env` exists — a file only `bootstrap-rundeck.sh` creates —
  and to 0 everywhere else. `lab-run.sh` lives in the repo and therefore also lives in
  every developer's working tree, and the refresh is a `git reset --hard` against
  `LAB_REPO`, which falls back to the script's own repo root. **This was not theoretical:
  during this build, executing `lab-run.sh` from the development checkout hard-reset it and
  destroyed uncommitted work.** A second, independent guard was added: the refresh refuses
  outright if the tree has uncommitted tracked changes. A bootstrapped runner never has
  any — its checkout is only ever written by git — so the guard costs nothing where the
  behaviour is wanted and prevents data loss everywhere else.
- **A nineteenth job, `Reimport Jobs`, was added.** The refresh keeps *playbooks* current;
  *job definitions* live in Rundeck's database and change only when something imports them.
  Without this, adding a job to the repo still meant an SSH session or a bootstrap re-run —
  a fifteenth manual step arriving by the back door. It reads `LAB_REPO` from the env file
  and `RUNDECK_API_TOKEN` from the checkout's `.env`, refreshes, and re-imports.
- **Semaphore gets no `lab-run.sh`.** It clones the repository itself before every task, so
  the checkout is current by construction and templates name the playbook directly. #11 does
  not exist on that path; #10 never did.
- **Steps 12–14 remain manual on the Semaphore path.** There is no `bootstrap-semaphore.sh`
  and this slice does not write one. `semaphore/README.md` now says so in its first
  paragraph rather than implying parity. This is a scope statement, not an oversight: the
  acceptance bar above is written against the Rundeck path, which is the one the live lab
  runs.
- **`config/` cannot reach Semaphore through git either**, since Semaphore re-clones per
  run. Its README now names the repository directory on the Semaphore host as the place to
  put it, instead of the previous (wrong) claim that it lives "in the repository checkout
  Semaphore clones".
