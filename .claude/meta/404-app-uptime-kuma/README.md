# 404 — Uptime Kuma role + playbook

**Status:** in-progress — implementation complete and gate-verified; awaiting live deploy acceptance. Decisions and deviations from the approach below are in notes.md.
**Depends on:** 303 (wiring), 401 (ntfy)
**Blocks:** auto-registered monitoring per CLAUDE.md

## Problem

Uptime Kuma is bootstrap step 5. No role or playbook exists.

Docker-on-LXC deployment.

## Files

To create:
- `ansible/roles/uptime-kuma/{tasks,handlers,defaults,meta,templates}/...`
- `ansible/roles/uptime-kuma/templates/docker-compose.yml.j2`
- `ansible/playbooks/apps/uptime-kuma.yml` (PATH A)
- `ansible/vars/app-defaults/uptime-kuma.yml`
- `config.example/apps/uptime-kuma.example.yml`

## Approach

1. Compose: `louislam/uptime-kuma:latest` with volume `uptime-kuma:/app/data`.
2. `docker compose up -d`.
3. Wait for HTTP 200 on `/`.
4. First-run setup is interactive in v1 — need to either:
   - Use the `uptime-kuma-api` Python lib to script setup (recommended)
   - OR document a one-time manual setup step (breaks the "1-click" promise)
   - OR check if Kuma v2 (currently beta) is mature enough — it has proper REST + setup-via-env
5. After setup, create the Ntfy notification channel — POST monitor-notification with `type: ntfy`, server: `homelabinfra_infra.notifications.ntfy_url`, topic: `homelab`.
6. Capture the notification channel ID.
7. Write to facts under the Shape B role key (superseding the `uptime_kuma:` sketch this
   README previously carried — slice 303 settled the key and `ansible/vars/CONTRACT.md`
   §3 documents it):
   ```yaml
   monitoring:
     provider: uptime_kuma
     instance: <proxmox hostname>
     host: https://status.<domain>      # full base URL including scheme
     token: <from-vault>
     notification_id: <ntfy channel id>
   ```
   `tasks/wiring/uptime-kuma.yml` targets the v2 REST surface behind a probe and skips
   with a warning when `GET /api/monitors` does not answer 200. Locking Kuma v1 here
   means replacing that file with a socket.io implementation — record the choice in this
   slice's `notes.md` and update slice 303.

Wire Caddy + Authentik (Kuma's own auth is fine, but homelab-users like SSO).

Implementation decision: lock in Kuma v1 + python lib, OR Kuma v2 if stable. Document choice in `notes.md`.

## Acceptance

- [ ] Kuma UI loads, initial admin user created without human intervention
- [ ] Ntfy notification channel configured and visible
- [ ] facts.yml has api_url, api_token, ntfy_notification_id
- [ ] Re-run is idempotent
