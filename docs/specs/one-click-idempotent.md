# Spec: one click, idempotent, notified

The supported operator path is one click per app in Rundeck — not grouped stack deploys or multi-job
chains. Re-running any playbook is always safe.

## Rule

- Each app is deployed by exactly one playbook run taking at most an `instance` parameter (plus
  optional per-job params documented in the playbook header). No manual steps between provision
  and a wired, monitored, running app.
- Every playbook is idempotent: re-running a deploy updates config/binary in place; re-running a
  wire confirms; re-running a remove is a no-op.
- Every automated state change calls the common notification task; read-only jobs (status,
  tail-applog) do not notify. Ntfy is the implemented publisher, and `none` is an explicit
  no-op. Gotify and Discord are reserved provider values until publishers exist. Failure paths
  must not send success notifications — a notify play that runs after a failed work play must
  check the result.
- Playbooks are UI-independent: nothing in `ansible/` may depend on Rundeck specifics.
  Supported job definitions live in `rundeck/`; `semaphore/` is an unverified reference.
- We configure tools, we do not replicate them: container updates belong to Watchtower, OS
  updates to unattended-upgrades, backups to PBS, uptime to Uptime Kuma.

## Enforced by

- inspection — cite this specification in findings
