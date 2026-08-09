# 010 — Config provenance: make bootstrap the author, and give config a way to travel

**Status:** built
**Subject:** Config model
**Related:** 012 (runner onboarding — landed together), 013 + 014 (Vaultwarden), 001 (loader)

## Goal

Nothing in the repo created, checked or could reconstruct `config/`. Every job ran only
because `config/proxmox.yml` happened to exist on one LXC — hand-written, gitignored,
unversioned, absent from every backup, and carrying a root-privileged Proxmox token in
plaintext. There was also no mechanism moving an edit from the workstation where config is
authored to the runner that executes it.

Five moves, all shipped:

1. **Bootstrap authors the config.** `bootstrap-rundeck.sh` runs as root on a PVE node, so
   most of `proxmox.yml` is discoverable (`pvesm`, `ip -o link`, hostname) rather than
   promptable. Six prompts remain, each also readable from an env var.
2. **Bootstrap mints the Proxmox token** via `pveum` and writes it straight to Key Storage —
   it never touches a file. This retires `root@pam!rundeck` for a scoped `homelab-infra@pve`.
3. **The runner's `config/` is the source of truth**, reached from the UI both ways by
   `Configure App` (writes `config/apps/<instance>.yml`), `Get Config` (reads it back,
   redacted) and `Config Doctor` (validates, mutates nothing).
4. **Durability from parts already owned** — PBS for off-host, `config/.backups/<file>.<ts>`
   (last 20) for per-edit history, a unified diff in the job log for visibility.
5. **The runner joins the model it runs** — tags its own LXC `homelab-infra`, writes
   `config/apps/rundeck.yml`, records a `runner` registry key. Until it did, the PBS backup
   job excluded the one guest holding the platform's own configuration.

The accepted cost: history is point-in-time, not per-commit-with-message. See
[../LESSONS.md](../LESSONS.md) for the config model as it now stands across slices.

## Remaining

The bootstrap script has run against real nodes — from a full wipe of the old runner and
its predecessor on 2026-08-01, exit 0 on three consecutive runs, and again since. `pveum`,
Key Storage staging, tagging and job import are all exercised. Evidence and the 15 defects
that run surfaced: [../012-runner-onboarding/notes.md](../012-runner-onboarding/notes.md).

- [x] `bash rundeck/bootstrap-rundeck.sh` on a bare node produces a runner whose
      `proxmox.yml` and `infrastructure.yml` are complete, with no human editing either —
      observed 2026-08-01, alongside `apps/rundeck.yml`
- [ ] That `proxmox.yml` contains **no secret**, and Lab Status runs green with
      `PROXMOX_API_TOKEN` supplied only from Key Storage
- [x] The platform's Proxmox credential is `homelab-infra@pve`, not `root@pam`, and its
      secret exists in exactly one place — role `HomelabInfra`, minted and rotated cleanly
      2026-08-01
- [ ] The runner LXC carries the `homelab-infra` tag, appears in Lab Status, and its vmid is
      in the PVE backup job created by `configure-pbs.yml`
- [x] `config/apps/rundeck.yml` and the `runner` registry key describe the running host —
      both authored 2026-08-01; `.generated/facts.yml` carries `runner` at the correct path
- [ ] `config-doctor` on the live runner's populated `config/` exits zero
- [ ] `Bootstrap Platform` aborts at the doctor play — not mid-provision — on a missing key
- [ ] Restoring the runner LXC from PBS yields a working runner with `config/` intact
- [ ] Re-running the bootstrap script rotates no credential and overwrites no answered prompt
- [x] `Configure App` writes the instance file, the log shows a unified diff, and the
      previous content lands in `config/apps/.backups/`
- [x] `Get Config` returns the authored file with `admin_token` redacted; with an `instance`
      it returns one file
- [x] A round trip holds — `Configure App` writes, `Get Config` returns what the survey said
- [x] `config-doctor` on an empty `config/` names every required key in one run, exits
      non-zero, mutates nothing

The four ticked items were verified on the workstation against a scratch `config/` tree,
`ansible-playbook` run directly rather than through Rundeck. That exercise found and fixed
four defects that would otherwise have shipped — see notes.md.

## Links

- `rundeck/bootstrap-rundeck.sh` — discovery, prompts, `pveum` token, Key Storage staging,
  runner self-adoption
- `ansible/tasks/config/write-config-file.yml` — the one path any playbook writes into
  `config/`: back up, write, diff, prune to 20
- `ansible/scripts/config-doctor.sh`, `ansible/scripts/redact-config.sh`
- `ansible/playbooks/maintenance/` — `config-doctor.yml`, `configure-app.yml`,
  `get-config.yml`, `reimport-jobs.yml`
- `ansible/scripts/with-proxmox-env.sh`, `ansible/tasks/load-user-vars.yml` — env-var
  fallback for the omitted secret
- `ansible/vars/CONTRACT.md` §3 (`runner` key), §5 (env secrets), `config/.backups/`
- [notes.md](notes.md) — the full defect analysis, the six design moves, decisions with
  their dates, and the deviations from plan with reasons
