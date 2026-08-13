# 502 — Rollback container playbook

**Status:** built
**Subject:** Day-2 ops
**Related:** 201 (Watchtower — the other half of the feedback loop), 501

## Goal

The recovery half of the day-2 feedback loop: Watchtower reports "updated", Kuma reports
"DOWN", and the notification the operator is already holding tells them to run this job.
`playbooks/stacks/rollback-container.yml` was a TODO header.

Parameters: `stack` and `container` (required), `image_tag` optional. On the stack host it
reads the current tag from `/opt/<container>/docker-compose.yml`, edits the `image:` line,
pulls, recreates, waits for health where the port is known, and posts an Ntfy message naming
both the old and new tags.

Omitting `image_tag` **pins to the currently running tag** — a freeze rather than a
rollback. Either way, pinning a non-`latest` tag disables Watchtower auto-update for that
container until the deploy playbook is re-run and restores the configured tag.

## Remaining

**None — closed 2026-08-12, executions 105, 106 and 111 against lidarr on `media_stack`.**

- [x] Rollback with an explicit tag changes the compose file and restarts the container —
      execution 106, `changed=2`: `Rewrite the image tag` and `Recreate the service`.
      Target was `3.1.0.4875-ls39`, the exact build `latest` already resolved to, so the
      test was real and the image identical
- [x] Rollback without a tag freezes at the current version — execution 105, `changed=0`,
      container never recreated, and the "no-op freeze on a floating tag" message fired
- [x] The Ntfy notification includes both old and new tags — `notify_message` carries
      `{{ _rb_old }} to {{ _rb_new }}` plus the pin/floating advice, and the publish
      returned OK under W6.5, where an undelivered notification is fatal
- [x] Idempotent on re-run against the same target tag — execution 111: rewrite skipped,
      `recreate: auto`, container untouched

**The run that mattered most was the one that passed while proving nothing.** Execution 106
recreated the container and exited 0 with the health probe SKIPPED, because the probe was
gated on `_rb_instance_config.app.port` and ports live in `vars/app-defaults/`, not in
instance files. W4's fatal gate — added so a rollback leaving a dead container cannot report
success — had been unreachable on every app since the day it was written. Fixed the same
session; execution 111 is the first run where it fired: `lidarr answered with HTTP 200
after the rollback.`

## Links

- `ansible/playbooks/stacks/rollback-container.yml`
- `rundeck/jobs/rollback-container.yaml` — keeps its parameters, unlike the per-app deploys
- [notes.md](notes.md) — decisions and deviations
