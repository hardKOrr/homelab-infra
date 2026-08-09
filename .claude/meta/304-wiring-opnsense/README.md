# 304 — OPNsense Unbound wire + unwire

**Status:** built
**Subject:** No live target
**Related:** 305 (the Pihole equivalent), 200 (the `dns` registry key)

## Goal

DNS resolution for deployed apps in OPNsense homelabs — the shape this lab actually runs.
Both halves were TODO headers. Both drive the OPNsense REST API with API key + secret basic
auth from the `dns` registry key, gated on `dns.provider == 'opnsense'`.

- **Wire** — `searchHost` with a hostname filter first to dedupe by UUID, then `addHost`
  with `{enabled: 1, hostname, domain, rr: A, server: <ip>}`, splitting `wiring_domain` into
  hostname and domain, then `service/reconfigure` to apply.
- **Unwire** — `searchHost` for the UUID, `delHost/<uuid>`, reconfigure.

Blocked on credentials, not on code: the lab has an OPNsense but this slice has never been
given its API key and secret.

## Remaining

- [ ] Wire produces a host override resolvable from clients using OPNsense DNS
- [ ] Re-wire is idempotent — no duplicate entries
- [ ] Unwire removes the entry; idempotent on missing
- [ ] `reconfigure` runs only when a change actually happened

## Links

- `ansible/tasks/wiring/opnsense.yml`, `ansible/tasks/unwiring/opnsense.yml`
- https://docs.opnsense.org/development/api/core/unbound.html
