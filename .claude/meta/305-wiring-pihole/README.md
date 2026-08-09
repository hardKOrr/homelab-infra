# 305 — Pihole wire + unwire

**Status:** built
**Subject:** No live target
**Related:** 304 (OPNsense — what this lab actually runs), 200 (the `dns` registry key)

## Goal

DNS resolution for Pihole-based homelabs; both halves were TODO headers. Targets **Pihole
v6's REST API** at `/api` — v5 used a URL-based `custom_records` hack, and a user still on
v5 upgrades or picks another provider. Auth is a session SID from `POST /api/auth`; gated on
`dns.provider == 'pihole'`.

- **Wire** — authenticate, `GET /api/config/dns/hosts`, add `<ip> <fqdn>` to the list if
  absent.
- **Unwire** — authenticate, remove the entry.

**No live target and expected to stay `built` indefinitely.** This lab runs OPNsense; low
priority unless a Pihole appears.

## Remaining

- [ ] Wire produces a host override resolvable from clients using Pihole DNS
- [ ] Re-wire is idempotent
- [ ] Unwire removes the entry; idempotent on missing
- [ ] Fails clearly with a "Pihole v6+ required" message against a non-v6 API

## Links

- `ansible/tasks/wiring/pihole.yml`, `ansible/tasks/unwiring/pihole.yml`
