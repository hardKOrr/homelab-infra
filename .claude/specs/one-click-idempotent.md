# Spec: one click, idempotent, notified

The product is one click per app in Semaphore/Rundeck — not grouped stack deploys, not multi-job
chains. Re-running any playbook is always safe.

## Rule

- Each app is deployed by exactly one playbook run taking at most an `instance` parameter (plus
  optional per-job params documented in the playbook header). No manual steps between provision
  and a wired, monitored, running app.
- Every playbook is idempotent: re-running a deploy updates config/binary in place; re-running a
  wire confirms; re-running a remove is a no-op.
- Every automated state change notifies via the configured notification provider (Ntfy by
  default); read-only jobs (status, tail-applog) do not notify. Failure paths must not send
  success notifications — a notify play that runs after a failed work play must check the result.
- Playbooks are UI-agnostic: nothing in `ansible/` may depend on Semaphore or Rundeck specifics;
  job definitions live in `semaphore/` and `rundeck/`.
- We configure tools, we do not replicate them: container updates belong to Watchtower, OS
  updates to unattended-upgrades, backups to PBS, uptime to Uptime Kuma.

## Enforced by

- inspection — cite this spec in findings (source: `.claude/CLAUDE.md` "Philosophy" and
  "Day-2 Operations")
