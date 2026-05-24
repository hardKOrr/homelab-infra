# 303 — Uptime Kuma wire + unwire

**Status:** open
**Depends on:** 200, 404 (uptime-kuma app)
**Blocks:** auto-registered monitoring per CLAUDE.md

## Problem

Both `tasks/wiring/uptime-kuma.yml` and `tasks/unwiring/uptime-kuma.yml` are TODO headers. Every app is supposed to auto-register.

## Files

- `ansible/tasks/wiring/uptime-kuma.yml` — implement
- `ansible/tasks/unwiring/uptime-kuma.yml` — implement

## Approach

**Verify Uptime Kuma API surface first.** Kuma v1 required socket.io; v2+ exposes a REST API. CLAUDE.md notes this caveat. If the Kuma version we install (slice 404) is v1, we may need the `uptime-kuma-api` Python helper called via `command:` instead of `uri:`. Decide once 404 is locked.

Assuming v2 REST:
**Wire:**
1. GET `/api/monitors`, filter by name == `wiring_app_name`.
2. If exists → PATCH; else POST with type=http, url=`wiring_monitor_url`, interval=`wiring_interval | default(60)`, notification_id_list=[homelab-ntfy-channel-id].
3. Resolve the Ntfy notification channel ID once and cache (likely fetched once in 404 and written to facts.yml).

**Unwire:**
1. Find by name → DELETE.

Not gated on a provider check — runs whenever Kuma is reachable (CLAUDE.md says all apps register).

## Acceptance

- [ ] Wire creates monitor with the correct URL and the Ntfy notification channel attached
- [ ] Down/up state changes trigger Ntfy messages
- [ ] Re-wire is idempotent
- [ ] Unwire deletes the monitor; idempotent on missing
- [ ] If Kuma is unreachable, wire fails gracefully (skip with warning rather than abort the whole deploy)
