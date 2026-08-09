# 012 — Runner onboarding: make the handover executable, not a checklist

**Status:** built
**Subject:** Config model
**Related:** 010 (config provenance — landed together, shares the edit surface), 601 (jobs)

## Goal

The promise is *clone, fill in two config files, run bootstrap*. The delivered path was
fourteen manual steps across three documents, one of them — refreshing the runner's
checkout — written down nowhere at all, with no README at the repository root.

Where 010 decides where a secret lives, this slice decides **who puts it there**. Four
moves:

- **Every remaining manual step earns its place or dies.** A step survives only if a machine
  cannot perform it or a human must decide something. Everything else moved into
  `bootstrap-rundeck.sh`, which now creates the project, stages Key Storage and imports
  every job over the REST API — no `rd` binary, no UI steps.
- **One wrapper, not sixteen copies.** `ansible/scripts/lab-run.sh` holds path resolution
  and the refresh; job files carry only a playbook name and arguments. Changing how jobs run
  is one edit — the lesson `059316a` already paid for once.
- **The checkout refreshes itself**, echoing the resolved commit into the job log so every
  execution records the revision that produced it. Safe because `config/` is gitignored and
  untracked, and 010 keeps it a plain tree rather than a nested checkout.
- **A root README** — the difference between a shareable project and a private one that
  happens to be on GitHub.

**Two bootstrap layers meet here.** The script on the PVE node builds the runner and
everything the UI needs to exist; `Bootstrap Platform` in the UI builds the lab. Every one
of the fourteen steps lands in one layer or the other — that is the acceptance bar, not a
reduction to some smaller number of chores.

The Rundeck git SCM plugin is dropped: it synced job *definitions*, never the working tree,
and the docs conflated the two. Jobs are imported one-way, and a job edited in the UI is
overwritten by the next reimport.

## Remaining

- [ ] Config authored on the runner is editable and readable from the UI alone — no SSH
      session anywhere in the documented path (010's Configure App / Get Config)
- [ ] `LAB_REFRESH=0` runs the on-disk checkout unchanged
- [ ] Re-running the bootstrap script against a configured Rundeck changes nothing and
      rotates no credential
- [ ] The repository root has a README that takes a first-time reader from clone to a
      running bootstrap
- [x] `bash rundeck/bootstrap-rundeck.sh` on a bare node yields a Rundeck with the project
      created, 19/19 jobs imported and Key Storage staged — 2026-08-01 on pve-host-3, after
      destroying the previous runner. Took fifteen fixes; see notes.md
- [x] The whole onboarding path is one command and one click — the click landed green
      2026-08-03, execution 12, all seven services. True of the finished artifact; the
      journey took a Vaultwarden cutover and three failed executions' worth of fixes
- [x] A commit pushed to `master` is executed by the next job run with no human action and
      the log names the commit — observed repeatedly 2026-08-03
- [x] No job file contains a `REPO` or `VENV` assignment

## Links

- `ansible/scripts/lab-run.sh` — the single job entry point. Ships in the repo and arrives
  via the clone; it is not hand-installed. `LAB_REFRESH` defaults to 1 **only** when
  `/etc/homelab-infra/lab-run.env` exists, and the refresh refuses on a tree with
  uncommitted tracked changes — both guards exist because the unconditional default
  destroyed uncommitted work in a development checkout
- `rundeck/bootstrap-rundeck.sh` — shared with 010; one rewrite, not two
- `rundeck/jobs/*.yaml` (22), `semaphore/project.json` — one `lab-run` call per step
- `rundeck/README.md`, `semaphore/README.md` — reduced to what cannot be automated, with a
  reason for each survivor. Semaphore steps 12–14 stay manual and say so; there is no
  `bootstrap-semaphore.sh` and this slice does not write one
- Repository root `README.md`
- [notes.md](notes.md) — the fifteen live-bootstrap fixes, the fourteen-step table with its
  new owners, the resolved open questions, and the deviations from plan
