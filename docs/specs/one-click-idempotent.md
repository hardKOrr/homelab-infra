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
- Prefer the component that already owns a concern: configure and integrate its behavior rather
  than duplicating it in Ansible. Project-owned automation is appropriate when orchestration or
  a repository contract has no existing owner.

## Automation boundary

Every agent action against the live lab goes through an Ansible playbook or a Rundeck job.
An agent must not use Rundeck's ad-hoc node command paths, including
`rd run -F '<filter>' -- <command>` or `rd run --script <path>`. An ad-hoc change is a
lab mutation that no playbook can reproduce, re-converge, or roll back, which directly
undermines this specification's idempotence guarantee. If the needed action has no job,
add the playbook and job instead of reaching around the automation.

Read-only inspection through the Rundeck API is allowed and is not a lab action. This
includes `jobs list`/`info`, `executions query`/`info`/`state`/`follow`, `nodes list`, and
`projects info`. The agent evidence workflow and its CLI details are in
[`docs/live-lab.md`](../live-lab.md).

## Enforced by

- inspection — cite this specification in findings
