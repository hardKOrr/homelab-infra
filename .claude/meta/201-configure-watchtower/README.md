# 201 — Implement configure-watchtower

**Status:** open
**Depends on:** 200 (needs ntfy facts), 401 (ntfy app must exist first in bootstrap order)
**Blocks:** Docker app stack hosts auto-updating

## Problem

`tasks/bootstrap/configure-watchtower.yml` is a TODO header only. Container auto-update is a core day-2 promise from CLAUDE.md.

## Files

- `ansible/tasks/bootstrap/configure-watchtower.yml` — implement
- Caller: any Docker host creation (likely a hook added to `playbooks/docker/create-docker-host.yml` or the bootstrap stack-host flow)

## Approach

Run on the target Docker host. Steps:

1. Create `/opt/watchtower/` (root:root, 0750).
2. Template `/opt/watchtower/docker-compose.yml` with:
   - Image `containrrr/watchtower`
   - `WATCHTOWER_NOTIFICATIONS=shoutrrr`
   - `WATCHTOWER_NOTIFICATION_URL=ntfy://{{ homelabinfra_infra.notifications.ntfy_url }}/{{ homelabinfra_infra.notifications.topic }}` — verify shoutrrr ntfy URL format
   - `WATCHTOWER_SCHEDULE="0 0 4 * * *"`
   - `WATCHTOWER_LABEL_ENABLE=true`
   - `WATCHTOWER_CLEANUP=true`
   - Notification template that includes the rollback instruction (per CLAUDE.md feedback loop)
   - Mount `/var/run/docker.sock:/var/run/docker.sock:ro`
3. `community.docker.docker_compose_v2` up.

## Acceptance

- [ ] Watchtower container running on the target host
- [ ] Test notification appears in Ntfy on first start
- [ ] An image update on a labeled container triggers an Ntfy message that includes the rollback hint
- [ ] Unlabeled containers are ignored (label_enable=true)
