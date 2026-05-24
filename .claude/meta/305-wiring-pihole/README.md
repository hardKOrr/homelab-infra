# 305 — Pihole wire + unwire

**Status:** open
**Depends on:** 200
**Blocks:** DNS resolution for Pihole-based homelabs

## Problem

Both `tasks/wiring/pihole.yml` and `tasks/unwiring/pihole.yml` are TODO headers.

## Files

- `ansible/tasks/wiring/pihole.yml` — implement
- `ansible/tasks/unwiring/pihole.yml` — implement

## Approach

Pihole v6 has a proper REST API at `/api`. v5 used a hacky URL-based custom_records mechanism. Target v6 — if a user is still on v5, they upgrade or use a different provider.

Auth: session token via POST `/api/auth` with the web password.

**Wire:**
1. Authenticate, capture session SID.
2. GET `/api/config/dns/hosts` — list of `<ip> <fqdn>` entries.
3. If `<wiring_ip> <wiring_hostname>` not present, PATCH the list with the entry added.
4. (Or use POST `/api/config/dns/hosts/<entry>` if the per-entry endpoint exists in v6.)

**Unwire:**
1. Authenticate.
2. DELETE `/api/config/dns/hosts/<entry>` or PATCH list with entry removed.

Gated on `homelabinfra_infra.dns.provider == 'pihole'`.

## Acceptance

- [ ] Wire produces a host override resolvable from clients using Pihole DNS
- [ ] Re-wire is idempotent
- [ ] Unwire removes the entry; idempotent on missing
- [ ] Fails clearly with a "Pihole v6+ required" message if the API endpoint is not v6
