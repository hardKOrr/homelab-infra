# 201 — notes

2026-07-24 — implementation complete; slice stays in-progress until live verification.

What's in:

- `tasks/bootstrap/configure-watchtower.yml` — writes `/opt/watchtower/docker-compose.yml`
  and brings it up via `community.docker.docker_compose_v2` (compose plugin is installed by
  the docker role). `WATCHTOWER_LABEL_ENABLE=true`, `WATCHTOWER_CLEANUP=true`, schedule
  default `0 0 4 * * *` (overridable via `watchtower_schedule`).
- Caller hook added: `playbooks/docker/create-docker-host.yml` play 3 now loads
  `homelabinfra_infra` and imports the task after the docker role.
- Shoutrrr URL built from Shape B facts: registry `host` carries a scheme (slice 200
  decision), shoutrrr wants a bare host — so
  `ntfy://<host-sans-scheme>/<topic>?scheme=<http|https>`.
- `WATCHTOWER_NOTIFICATION_TEMPLATE` (Go template, `{% raw %}`-guarded from Jinja) appends the
  rollback instruction per the CLAUDE.md feedback loop.
- No-notifications degradation: if provider != ntfy or facts absent, Watchtower still deploys
  and updates — it just doesn't notify (matches the wiring "missing providers are no-ops" rule).

Verified: ansible-lint + syntax-check gates green (create-docker-host.yml covered by the
syntax gate). NOT yet verified live — acceptance items (container running, Ntfy test message,
update notification with rollback hint, unlabeled containers ignored) need a Docker host and
a bootstrapped Ntfy (slice 401). Flip to done after the first real docker-host deploy.

## 2026-08-10 — closed without the update-triggered observation

Closed by decision, not by ticking the last two boxes. The remaining criteria —
"an image update on a labeled container triggers an Ntfy message" and "unlabeled
containers are ignored" — test whether Watchtower does what Watchtower advertises.
This slice's code is a Compose service definition plus a notification URL. If those
criteria failed, nothing in this repo would change; the fix would be upstream or a
different tool.

Verified by inspection instead: the container is defined with the notification URL
`configure-watchtower.yml` builds, label filtering is `WATCHTOWER_LABEL_ENABLE`, and
the rollback hint is in the notification template. That is the whole of what this
slice controls.

Kept as a real check, moved to INDEX's "observe if it happens" list: whenever an image
genuinely moves, confirm the Ntfy message carries the rollback hint. Nothing waits on it.
