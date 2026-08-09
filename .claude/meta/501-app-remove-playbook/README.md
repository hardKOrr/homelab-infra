# 501 — App removal playbook

**Status:** built
**Subject:** Day-2 ops
**Related:** 300–305 (the unwire halves), 303 (the Kuma defect this run exposed), 502

## Goal

`playbooks/apps/remove.yml` only loaded config and asserted. It now runs three plays
mirroring the deploy in reverse:

1. **Unwire** on localhost — the matching `unwiring/<provider>.yml` for reverse proxy, SSO
   (by identity mode), Uptime Kuma and DNS, each gated on its provider.
2. **Stop and remove** on the target host — `docker compose down` for Docker apps,
   `systemctl stop`/`disable` for native. Native apps stop by `app.service_name`, which is
   not always the app name (PBS runs `proxmox-backup-proxy`).
3. **Notify** — an Ntfy post.

Three deliberate limits: `delete_data: false` by default, so re-running the deploy restores;
**stack hosts are never destroyed** even when empty; and `config/apps/<instance>.yml` is
never deleted, because it is the restore point.

Ran live 2026-08-02 against the whole baseline. Four items met, one disproved — and **the
disproving defect is fixed** (2026-08-03): `unwiring/caddy.yml` and `unwiring/authentik.yml`
now probe first and degrade on an unreachable provider, matching `unwiring/uptime-kuma.yml`,
and the `no_log: true` that censored the Authentik lookup's own failure is off.

## Remaining

- [ ] Re-running remove on an already-removed app is idempotent — reopened by the live run,
      fixed in code, **not yet re-observed.** A removal against a stopped Caddy or Authentik
      is what closes this
- [x] Removing a Docker app stops and removes the container and unwires everything
- [x] Removing a native LXC app stops the service and unwires everything
- [x] `config/apps/<instance>.yml` survives
- [x] The Ntfy notification fires

The Kuma monitor was not actually deleted during that run, but that was never this slice's
defect: `GET /api/monitors` answered 200 with the SPA's HTML, so the probe, the delete and
the verify assert all passed without touching a monitor. Owned and fixed by **303**.

## Links

- `ansible/playbooks/apps/remove.yml`
- `ansible/tasks/unwiring/` — all provider halves
- `ansible/vars/app-defaults/*.yml` — `app.service_name` on native apps
- [notes.md](notes.md) — the live-run findings
