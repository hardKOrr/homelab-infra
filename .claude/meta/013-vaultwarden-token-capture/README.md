# 013 — Vaultwarden admin token self-capture: collapse the two-pass bootstrap

**Status:** built, unverified on live hardware
**Depends on:** 400 (Vaultwarden app), 500 (bootstrap plays)
**Blocks:** 010 (config provenance) — the unattended bootstrap path; 014 (Vaultwarden as
the generated-secret store)

## Problem

`bootstrap.yml` halts mid-run and waits for a human. Vaultwarden deploys, prints its admin
token to the job console once, and the play stops at a gate until the operator pastes that
token into `config/infrastructure.yml` and re-runs (`bootstrap.yml:20-24`).

That design is defensible when a person is watching a terminal. It fails in every direction
the project is now moving:

- **It cannot be driven from a UI.** A Rundeck or Semaphore execution has no one to paste
  into it. Bootstrap triggering the Vaultwarden deploy as a job — slice 010's step 4 —
  would have to poll the execution and *scrape the console output for a secret*. That works
  and is fragile: it depends on log formatting, it writes the secret into the execution log
  where it persists, and it breaks on any change to the task's output.
- **It makes the secret's only durable copy a human's clipboard.** Between the print and
  the paste, the token exists in exactly one place that is not a system.
- **It contradicts the one-click promise** at the single most important run in the project.

The console print is also the *only* record. `roles/vaultwarden` does not write the token
anywhere a machine can read it back.

## Approach

**Make the deploy write the token; make the gate read it.**

- `roles/vaultwarden` writes the generated admin token to a machine-readable sink at
  creation — Rundeck Key Storage (`keys/vaultwarden/admin-token`) / the Semaphore
  environment, matching where 010 puts the Proxmox token. Same mechanism, same backup path,
  one concept rather than two.
- The bootstrap gate reads that sink instead of asserting on
  `homelabinfra_config.infrastructure.vaultwarden.admin_token`. When the sink holds a token,
  the run continues; the two-pass flow collapses to one pass.
- The console print stays, as a convenience for an operator watching a terminal. It stops
  being the mechanism.
- `config/infrastructure.yml` keeps `vaultwarden.admin_token` as an accepted override, so an
  existing lab that already pasted a token is unaffected by a `git pull`.
- Precedence, highest first: explicit config value → environment/Key Storage → fail with the
  paste-and-re-run message the gate prints today.

**Silence notifications for this run.** Ntfy does not exist yet at bootstrap step 1. Verify
`tasks/notify.yml` and every consumer no-op on absent `homelabinfra_infra.notifications`
rather than erroring or hanging on a connection attempt — an unattended bootstrap has no one
to notice a stall.

## Files

- `ansible/roles/vaultwarden/tasks/main.yml` — write the generated admin token to the
  configured sink at creation; keep the console print as secondary output.
- `ansible/playbooks/bootstrap.yml:70-90` — the token gate reads the sink; the two-pass
  comment block at `:20-24` is rewritten to describe a single pass.
- `ansible/tasks/notify.yml` — assert the no-op path on absent notification facts.
- `rundeck/bootstrap-rundeck.sh`, `semaphore/project.json` — stage
  `keys/vaultwarden/admin-token` as a writable entry before the first bootstrap run.
- `config.example/infrastructure.yml` — document `vaultwarden.admin_token` as an optional
  override rather than a required paste.

## Acceptance

- [ ] `bootstrap.yml` runs from an empty lab to a deployed Vaultwarden and past the gate in
      **one execution**, with no human input — *needs a live run*
- [x] The admin token is readable from the sink after that run — round-tripped on both
      sink paths (`secrets.d/`, `config/.generated/`) and through `lab-run.sh`
- [x] The admin token does **not** appear in the job execution log — the plaintext print
      is now reachable only when the sink write fails
- [x] A lab with `vaultwarden.admin_token` already set in `config/infrastructure.yml` still
      bootstraps unchanged — precedence verified: env > config file > sink
- [x] With no token available from any source, the gate fails with actionable instructions
      rather than hanging
- [x] Bootstrap step 1 emits no notification attempt and does not stall on the absent Ntfy

## What was built

- `ansible/tasks/vaultwarden/token-sink.yml` (new) — resolves the sink path and reads the
  token back. One file owns the sink order; the role and the gate both import it.
- `ansible/roles/vaultwarden/tasks/main.yml` — writes the token to the sink on the run that
  generates it; the console print is now the fallback for a failed write.
- `ansible/playbooks/bootstrap.yml` — the gate resolves env → config → sink, continues in
  one pass, and publishes the token into `homelabinfra_config` for later plays.
- `ansible/scripts/lab-run.sh` — sources every `*.env` in `secrets.d/` alongside
  `secrets.env`.
- `rundeck/bootstrap-rundeck.sh` — creates `/etc/homelab-infra/secrets.d` (0700
  rundeck:rundeck) so a job can write it without sudo.
- `ansible/scripts/config-doctor.sh` — a populated sink satisfies the admin-token check.
- `ansible/tasks/notify.yml` — the enabled check moved ahead of the input assert, so the
  no-op path no longer templates a caller's message built from absent facts.

## Decisions taken during the build

- **Sink is a file, not Rundeck Key Storage.** Key Storage would need the Rundeck API URL
  and a token inside every playbook run — new plumbing in exactly the place slice 012 was
  removing it from. A `secrets.d/` file reuses the mechanism `secrets.env` already
  established and is sourced by the same code path.
- **`secrets.d/` is owned by the job user, `secrets.env` stays root-owned.** Operator-
  supplied secrets and platform-generated ones have different writers. A playbook running
  as `rundeck` must be able to create the token file without privilege escalation.
- **The console print became conditional.** The slice text asked to keep it as a
  convenience and also asked that the token not reach the log; those conflict. It now
  fires only when the sink write failed, which is the one case where the log is the token's
  only surviving copy.

## Verified locally, not on the runner

Round trip on both sink paths, precedence across all four combinations, `lab-run.sh`
sourcing in four runner states, config-doctor in four states, and the failed-write branch.
`ansible-lint` and `--syntax-check` pass. **Nothing here has run against live Proxmox** —
the first bootstrap on real hardware is still the experiment.

One defect was caught in testing and fixed: the sink write originally used
`failed_when: false`, which forces `.failed` to `False` even when the module errored. That
made the fallback print unreachable and the success message untrue on the exact path it
existed to cover. It now uses `ignore_errors` and the branch was tested both ways.
