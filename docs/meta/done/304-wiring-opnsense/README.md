# 304 — OPNsense Unbound wire + unwire

**Status:** closed 2026-08-12 — verified live against the lab's OPNsense at 192.168.13.1
**Subject:** Networking
**Related:** 305 (the Pihole equivalent), 200 (the `dns` registry key)

## Goal

DNS resolution for deployed apps in OPNsense homelabs — the shape this lab actually runs.
Both halves were TODO headers. Both drive the OPNsense REST API with API key + secret basic
auth from the `dns` registry key, gated on `dns.provider == 'opnsense'`.

- **Wire** — `searchHost` with a hostname filter first to dedupe by UUID, then `addHost`
  with `{enabled: 1, hostname, domain, rr: A, server: <ip>}`, splitting `wiring_domain` into
  hostname and domain, then `service/reconfigure` to apply.
- **Unwire** — `searchHost` for the UUID, `delHost/<uuid>`, reconfigure.

**It was never blocked.** `OPNSENSE_KEY` and `OPNSENSE_SECRET` had been sitting in the repo's
gitignored `.env` for days while INDEX listed this slice as waiting on credentials. Cost:
nothing, because nothing depended on it — but "blocked on X" is a claim that expires, and
this one was checked by reading a file.

## Remaining

**None — closed 2026-08-12.** Exercised against a throwaway record,
`labprobe.wasitacatisaw.cc`, so the router's live overrides were never touched. One probe
ran wire → wire again → wire with a different IP → unwire → unwire again, `changed=6`, which
is create+apply, nothing, repoint+apply, delete+apply.

- [x] Wire produces a host override resolvable from clients using OPNsense DNS — resolved
      to **192.168.0.10**, the reverse proxy, which is what `_opn_ip` intends: apps are
      reached through the proxy, not at their own address
- [x] Re-wire is idempotent — no duplicate entries — the second pass issued zero writes
- [x] Unwire removes the entry; idempotent on missing — NXDOMAIN after, and the second
      unwire skipped both the delete and the reconfigure
- [x] `reconfigure` runs only when a change actually happened — skipped on both no-op passes

Every write calls Unbound's `reconfigure`, which reloads the resolver. On a live router that
is a brief LAN-wide DNS blip, so a deploy that wires many apps at once reloads many times.
Not a defect; worth knowing before running one against a busy network.

## Links

- `ansible/tasks/wiring/opnsense.yml`, `ansible/tasks/unwiring/opnsense.yml`
- https://docs.opnsense.org/development/api/core/unbound.html
