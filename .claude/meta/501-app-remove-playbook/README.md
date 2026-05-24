# 501 — App removal playbook

**Status:** open
**Depends on:** 300-305 (unwire halves of each wiring slice)
**Blocks:** Remove App job in Semaphore/Rundeck

## Problem

`playbooks/apps/remove.yml` currently only loads config + asserts. The actual removal logic is a TODO.

## Files

- `ansible/playbooks/apps/remove.yml` — implement

## Approach

Three plays matching the deploy's three plays in reverse:

**Play 1 — Unwire (on localhost):**
- Load `homelabinfra_infra`
- Load app_config (same merge as deploy)
- Include the matching unwire tasks (each gated on provider):
  - `tasks/unwiring/{{ homelabinfra_infra.reverse_proxy.provider }}.yml`
  - `tasks/unwiring/authentik.yml` if SSO provider is authentik AND `app_config.routing.auth` is true
  - `tasks/unwiring/uptime-kuma.yml`
  - `tasks/unwiring/{{ homelabinfra_infra.dns.provider }}.yml`

**Play 2 — Stop and remove the app (on target host):**
- For Docker apps: `docker compose -f /opt/<instance>/docker-compose.yml down -v` (or without -v to preserve data — make this a parameter)
- For native LXC: `systemctl stop <service>`, `systemctl disable <service>`, optionally delete the binary and config

**Play 3 — Notify (on localhost):**
- Ntfy POST: "<instance> removed"

Decisions:
- `delete_data: false` default — preserves data, user re-running deploy restores. `delete_data: true` for hard wipe.
- Stack hosts are NOT destroyed even if empty — explicit user decision.
- `config/apps/<instance>.yml` is NOT deleted — it's the restore point.

How to detect Docker vs native? Read `app_config.proxmox.type` if set, OR check for `app_config.stack`. Document the heuristic.

## Acceptance

- [ ] Removing a Docker app stops + removes the container, unwires Caddy/Authentik/Kuma/DNS
- [ ] Removing a native LXC app stops the service, unwires everything
- [ ] Re-running remove on an already-removed app is idempotent
- [ ] `config/apps/<instance>.yml` survives
- [ ] Ntfy notification fires
