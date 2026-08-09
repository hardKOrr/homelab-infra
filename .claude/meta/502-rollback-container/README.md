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

Live acceptance needs a Docker app actually rolled back a tag.

- [ ] Rollback with an explicit tag changes the compose file and restarts the container
- [ ] Rollback without a tag freezes at the current version
- [ ] The Ntfy notification includes both old and new tags
- [ ] Idempotent on re-run against the same target tag

## Links

- `ansible/playbooks/stacks/rollback-container.yml`
- `rundeck/jobs/rollback-container.yaml` — keeps its parameters, unlike the per-app deploys
- [notes.md](notes.md) — decisions and deviations
