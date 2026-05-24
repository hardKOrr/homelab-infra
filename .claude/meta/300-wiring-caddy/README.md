# 300 — Caddy wire + unwire

**Status:** open
**Depends on:** 200
**Blocks:** 402 (caddy app), 504 (media stack wire)

## Problem

Caddy is the default reverse proxy and the most-used wiring target. Both `tasks/wiring/caddy.yml` and `tasks/unwiring/caddy.yml` are TODO headers only.

## Files

- `ansible/tasks/wiring/caddy.yml` — implement
- `ansible/tasks/unwiring/caddy.yml` — implement

## Approach

Both via Caddy admin API (`homelabinfra_infra.caddy.admin_api_url`, typically `http://CADDY_IP:2019`).

**Wire:**
1. Build route JSON: match `wiring_domain`, reverse_proxy to `wiring_upstream_host:wiring_upstream_port`, ACME for TLS.
2. Check if route with the same `@id` (`route_<wiring_app_name>`) exists — GET `/id/route_<name>`.
3. If exists → PATCH; else POST to `/config/apps/http/servers/srv0/routes`.
4. Verify route active (GET, expect 200).

**Unwire:**
1. GET `/id/route_<wiring_app_name>`. 404 → no-op success.
2. DELETE `/id/route_<wiring_app_name>`.

Use `ansible.builtin.uri`. All wiring tasks gated on `homelabinfra_infra.reverse_proxy.provider == 'caddy'`.

## Acceptance

- [ ] Wiring a fresh app produces a working HTTPS route via Caddy
- [ ] Re-running wire is a no-op (idempotent — PATCH same content = no change)
- [ ] Unwiring removes the route; the domain returns Caddy's default response (or 404)
- [ ] Unwiring a non-existent route succeeds (idempotent)
- [ ] Both tasks are gated on the provider check and no-op if a different provider is configured
