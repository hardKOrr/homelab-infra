# 010 — Config provenance: stop the one-click platform resting on an unversioned file

**Status:** open
**Depends on:** 000 (variable-loading contract), 001 (config loader)
**Blocks:** any honest claim that this repo is shareable or reproducible

## Problem

Every job in both UIs runs only because `config/proxmox.yml` happens to exist on one
LXC. That file is hand-written, gitignored, unversioned, unvalidated, absent from every
backup, and carries a root-privileged Proxmox API token in plaintext. Nothing in the
repo creates it, checks it, or can reconstruct it. If 13228 is lost, the platform's
operating credentials are lost with it and no record survives of what they were.

This is not one stray file. The same shape repeats across the system:

| File | Where it lives | Who wrote it | Recoverable? |
|---|---|---|---|
| `config/proxmox.yml` | runner LXC only | a human, once | no |
| `config/infrastructure.yml` | runner LXC only | a human, once | no |
| `config/.generated/facts.yml` | runner LXC only | bootstrap | only by re-running bootstrap |
| `.env` | one workstation | a human, once | no |

The user-facing promise is "clone, fill in two config files, run bootstrap." The reality
is that filling them in is unguided, unvalidated, and produces state that exists in
exactly one place. A wrong or missing key surfaces as a failure deep inside a playbook —
slice 601's first live run died at `ModuleNotFoundError` inside a config parser, which is
the polite version of the same problem.

Two failures are being conflated and need separating:

1. **Shape** — api_host, node, networks, ssh_public_key. Not secret. Belongs in review,
   in git history, in a backup. There is no reason this is invisible.
2. **Secret** — `api_token_secret`, `vaultwarden.admin_token`. Genuinely must not be in
   git, and the project already sanctions the alternative: CLAUDE.md's secrets model says
   these live "in gitignored `config/` files **or** Semaphore env vars."

Today both are fused into one opaque file, so the secret's constraint is imposed on the
shape, and the shape's invisibility is inherited by the whole platform.

## Files

- `ansible/scripts/with-proxmox-env.sh` — fall back to `PROXMOX_API_TOKEN` (and
  `PROXMOX_API_TOKEN_ID`) from the environment when the config file omits the secret;
  today it hard-fails on a missing key
- `ansible/tasks/load-user-vars.yml` — same fallback for the playbook side
- `ansible/scripts/config-doctor.sh` (new) — validate a `config/` against
  `vars/CONTRACT.md`; report every missing/malformed key at once, with the file and key
  path, and exit non-zero
- `ansible/playbooks/maintenance/config-doctor.yml` (new) — the same check as a job, so
  the answer to "is this runner configured?" is one click, not an SSH session
- `rundeck/jobs/*.yaml`, `semaphore/project.json` — inject the secret from Key
  Storage / env into the step environment; add the Config Doctor job
- `config.example/proxmox.yml` — document the empty-secret + env-var form as the
  recommended shape for a UI runner
- `rundeck/README.md`, `rundeck/bootstrap-rundeck.sh` — stage the Key Storage entry;
  document what to back up and how to restore it

## Approach

- **Split shape from secret.** `api_token_secret` becomes optional in the file. When it
  is absent, the wrapper and the loader read `PROXMOX_API_TOKEN` from the environment.
  A runner then holds a config file that is safe to read, diff, copy and archive.
- **Put the secret where the platform already backs things up.** Rundeck Key Storage
  (`keys/proxmox/api-token`) and the Semaphore equivalent, injected per step. Both survive
  their instance's own backup; a file in `/var/lib/rundeck` does not.
- **Make the shape reproducible.** A committed, non-secret
  `config.example/proxmox.<lab>.yml` per real lab, or a documented "copy config/ minus
  secrets into your own private repo" path. Decide which — see Open questions.
- **Fail at the front door.** `config-doctor` runs before anything mutates: every missing
  key reported at once, named by file and path, in one pass. No more discovering key three
  after fixing keys one and two.
- **Say what to back up.** One section in `rundeck/README.md`: these paths, this Key
  Storage entry, restore in this order.

## Acceptance

- [ ] A runner with `api_token_secret` absent from `config/proxmox.yml` and
      `PROXMOX_API_TOKEN` in the job environment runs Lab Status green
- [ ] `config-doctor` on an empty `config/` names every required key in one run, exits
      non-zero, and mutates nothing
- [ ] `config-doctor` on the live runner's populated `config/` exits zero
- [ ] Deleting and restoring `config/` from documented sources leaves a working runner
- [ ] `config/proxmox.yml` on the live runner contains no secret

## Open questions

- **Where does the non-secret shape live?** A per-lab example in this repo is public and
  reviewable but leaks internal topology to anyone cloning. A private companion repo keeps
  it out of sight but reintroduces a second thing to remember. Operator's call.
- **Does `config/.generated/facts.yml` get the same treatment?** It is machine-written and
  rebuildable by re-running bootstrap, so it may be acceptable as runner-local state — but
  it holds service tokens, and "rebuildable" means "redeploy the platform," which is not a
  recovery plan.
