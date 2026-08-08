# 404 — Uptime Kuma role + playbook

**Status:** built — **reopened and then largely closed on 2026-08-08.** The app had never initialized: Kuma 2 asks for a database backend before it will do anything, the role did not answer, and it sat on its setup screen for five days across four green bootstrap runs. It is now initialized, has an admin account, and has a REST API key — all scripted, all verified live (executions 36–38, the last converging to `changed=0`). What is left is not in this slice: monitor registration is socket.io-only in every Kuma version, and moving the wiring onto it is **slice 303**.
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

- [x] Kuma UI loads, initial admin user created without human intervention — **observed
      2026-08-08, execution 36.** The role answers the database step
      (`POST /setup-database {"dbConfig":{"type":"sqlite"}}`) and then creates the account
      over socket.io. The instance now reports `42["loginRequired"]` on connect where it
      reported `42["setup"]` before, and its database went from absent to 286 KB
- [x] An API key exists without human intervention — **execution 37, and this was
      supposed to be impossible.** The belief that Kuma issues keys only to a browser
      session was wrong: it issues them to an authenticated *socket*, and `login` then
      `addAPIKey` over the same long-polling transport mints one. The old manual
      three-step instruction is now a fallback that only prints if that fails
- [ ] Ntfy notification channel configured and visible — reachable now that the app is
      initialized, but not yet observed
- [x] The monitoring endpoint and key are recorded — in `monitoring` plus the vault
      item `homelab-infra/monitoring`. The original criterion named `api_url`,
      `api_token` and `ntfy_notification_id` in facts.yml; slices 200 and 014 moved that
      shape, and it is recorded met against the shipped one
- [x] Re-run is idempotent — execution 38, `changed=0`, reusing the recorded key. Unlike
      executions 31 and 32, which also said `changed=0` against an app that had never
      started, this converges on a *working* instance

### What is left, and where it belongs

Monitor registration. Uptime Kuma exposes no REST endpoint for monitor CRUD in **any**
version — `GET /api/monitors` answers 200 `text/html` because the front end is served
from a catch-all route, which is what made the wiring's probe, delete and verify-assert
all pass against nothing. That is **slice 303's** rework, and it is now unblocked in the
way that matters: this slice proves socket.io is drivable from Ansible with four `uri`
tasks and no Python client, which is the dependency 303 rejected Kuma v1 to avoid.

### The lesson this slice paid for

A deploy that checks only "is the container up" cannot tell a working application from
one waiting for a human, and this one went green four times while doing nothing. Worse,
the role's own setup step read Kuma 2's `404 Cannot POST /setup` as "already
initialised" — **an error code interpreted as success**. Every app role wants one check
that only an initialized application can pass; for this one that check is
`GET /api/entry-page`.
