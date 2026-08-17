# 013 — Vaultwarden admin token self-capture: collapse the two-pass bootstrap

**Status:** done
**Subject:** Vaultwarden
**Related:** 400 (the app), 016 (moves this token into bounded Key Storage), 014

## Goal

`bootstrap.yml` used to halt mid-run and wait for a human: Vaultwarden deployed, printed its
admin token to the console once, and the play stopped until the operator pasted it into
`config/infrastructure.yml` and re-ran. That cannot be driven from a UI, it makes the
secret's only durable copy a clipboard, and it contradicts the one-click promise at the most
important run in the project.

**Make the deploy write the token; make the gate read it.** The role writes the generated
token to a machine-readable sink at creation, and the bootstrap gate reads that sink instead
of asserting on config. Precedence, highest first: explicit config value → environment/sink
→ fail with actionable instructions. `config/infrastructure.yml` keeps
`vaultwarden.admin_token` as an accepted override, so an existing lab is unaffected by a
`git pull`. The console print survives only as the fallback when the sink write fails —
which is the one case where the log is the token's only surviving copy.

Notifications are silenced for this run: Ntfy does not exist yet at bootstrap step 1, and an
unattended bootstrap has no one to notice a stall.

**This is capture, not a complete credential design.** `secrets.d/vaultwarden.env` is a
temporary bootstrap sink; 016 moves it into bounded Key Storage once HTTPS exists. The
admin-panel token cannot authenticate a Bitwarden vault client, so this does not by itself
unblock 014.

## Remaining

- [x] The fresh-lab flow deployed Vaultwarden, persisted its admin token, and a later
      `bootstrap.yml` job consumed that token without a paste or token-specific re-run —
      2026-08-01. `bootstrap-rundeck.sh` wrote
      `/etc/homelab-infra/secrets.d/vaultwarden.env` (0600 `rundeck:rundeck`) and returned
      exit 0. The first `Bootstrap Platform` execution then crossed the sink-backed admin
      token gate. Execution 7 completed all baseline services in one Rundeck job. These were
      two workflow executions: the runner bootstrap had already deployed Vaultwarden before
      `bootstrap.yml` ran. Do not describe this as one process from bare Proxmox through the
      full platform
- [x] The admin token is readable from the sink after that run — round-tripped on both sink
      paths and through `lab-run.sh`
- [x] The token does **not** appear in the job execution log
- [x] A lab with `vaultwarden.admin_token` already set still bootstraps unchanged —
      precedence verified: env > config file > sink
- [x] With no token from any source, the gate fails with actionable instructions rather than
      hanging
- [x] Bootstrap step 1 emits no notification attempt and does not stall on the absent Ntfy

The sink mechanics were verified on the workstation across both sink paths, all four
precedence combinations, four runner states and the failed-write branch. The producer and
consumer path was then verified on live Proxmox on 2026-08-01.

## Links

- `ansible/tasks/vaultwarden/token-sink.yml` — one file owns the sink order; the role and
  the gate both import it
- `ansible/roles/vaultwarden/tasks/main.yml`, `ansible/playbooks/bootstrap.yml`
- `ansible/scripts/lab-run.sh` — sources every `*.env` in `secrets.d/` alongside
  `secrets.env`
- `ansible/scripts/config-doctor.sh`, `ansible/tasks/notify.yml`
- `rundeck/bootstrap-rundeck.sh` — creates `secrets.d/` 0700 `rundeck:rundeck` so a job can
  write it without sudo
- [notes.md](notes.md) — the two-pass problem in full, the build record, the decisions
  (including why the sink is a file rather than Key Storage), and the `failed_when: false`
  defect caught in testing
