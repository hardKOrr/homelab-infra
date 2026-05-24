# 304 — OPNsense Unbound wire + unwire

**Status:** open
**Depends on:** 200
**Blocks:** DNS resolution for deployed apps in OPNsense homelabs

## Problem

Both `tasks/wiring/opnsense.yml` and `tasks/unwiring/opnsense.yml` are TODO headers. User's homelab uses OPNsense (per memory).

## Files

- `ansible/tasks/wiring/opnsense.yml` — implement
- `ansible/tasks/unwiring/opnsense.yml` — implement

## Approach

OPNsense REST API. Auth = API key + secret pair (basic auth).

Inputs from `homelabinfra_infra.dns`: `host`, `api_key`, `api_secret`.

**Wire (Unbound host override):**
1. POST `/api/unbound/host/addHost` with `{enabled: 1, hostname: <hostname>, domain: <domain>, rr: A, server: <ip>}` — splits `wiring_domain` into hostname + domain.
2. Check for duplicate first: POST `/api/unbound/host/searchHost` with the hostname filter, dedupe by UUID.
3. POST `/api/unbound/service/reconfigure` to apply.

**Unwire:**
1. searchHost → get UUID.
2. POST `/api/unbound/host/delHost/<uuid>`.
3. reconfigure.

Gated on `homelabinfra_infra.dns.provider == 'opnsense'`.
Docs: https://docs.opnsense.org/development/api/core/unbound.html

## Acceptance

- [ ] Wire produces a host override resolvable from clients using OPNsense DNS
- [ ] Re-wire is idempotent (no duplicate entries)
- [ ] Unwire removes the entry; idempotent on missing
- [ ] reconfigure runs only when a change actually happened
