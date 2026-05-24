# 404 — Uptime Kuma role + playbook

**Status:** open
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
7. Write to facts:
   ```yaml
   uptime_kuma:
     api_url: https://status.<domain>
     api_token: <from-vault>
     ntfy_notification_id: <id>
   ```

Wire Caddy + Authentik (Kuma's own auth is fine, but homelab-users like SSO).

Implementation decision: lock in Kuma v1 + python lib, OR Kuma v2 if stable. Document choice in `notes.md`.

## Acceptance

- [ ] Kuma UI loads, initial admin user created without human intervention
- [ ] Ntfy notification channel configured and visible
- [ ] facts.yml has api_url, api_token, ntfy_notification_id
- [ ] Re-run is idempotent
