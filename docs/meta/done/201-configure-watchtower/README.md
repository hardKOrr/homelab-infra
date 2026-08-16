# 201 — Implement configure-watchtower

**Status:** built
**Subject:** Day-2 ops
**Related:** 401 (Ntfy must exist first in bootstrap order), 200 (notification facts), 502

## Goal

Container auto-update is a core day-2 promise: configure the tool, don't replicate it.
`tasks/bootstrap/configure-watchtower.yml` was a TODO header.

Watchtower runs on each Docker stack host from `/opt/watchtower/docker-compose.yml`, on a
nightly schedule, with `WATCHTOWER_LABEL_ENABLE=true` so it touches only containers that opt
in, and `WATCHTOWER_CLEANUP=true`. Notifications go through shoutrrr to Ntfy, built from
`notifications.host` + `.topic` — the registry stores no pre-built URL.

The notification template carries the **rollback instruction**, which is the other half of
the day-2 feedback loop: Watchtower says "updated", Kuma says "DOWN", and the message the
operator is already holding tells them how to run the Rollback job.

## Remaining

Live acceptance needs a container update actually reported.

- [ ] Watchtower container running on the target host
- [ ] Test notification appears in Ntfy on first start
- [ ] An image update on a labeled container triggers an Ntfy message including the
      rollback hint
- [ ] Unlabeled containers are ignored

## Links

- `ansible/tasks/bootstrap/configure-watchtower.yml`
- `ansible/playbooks/docker/create-docker-host.yml` — the caller in the stack-host flow
- `ansible/vars/CONTRACT.md` §3 — `notifications` gained optional `user`/`password`/`token`;
  consumers fall back to anonymous POST when no token is recorded
