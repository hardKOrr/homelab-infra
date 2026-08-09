# 300 — Caddy wire + unwire

**Status:** built
**Subject:** Caddy / TLS
**Related:** 306 (forward_auth handler chain), 407 + 015 (certificates), 504 (media stack)

## Goal

Caddy is the default reverse proxy and the most-used wiring target; both halves were TODO
headers. Both drive Caddy's admin API (`reverse_proxy.host`, typically port 2019) with
`ansible.builtin.uri`, gated on `reverse_proxy.provider == 'caddy'`.

- **Wire** — build the route JSON (match `wiring_domain`, reverse_proxy to
  `wiring_upstream_host:wiring_upstream_port`), `GET /id/route_<app>`; PATCH when it exists,
  POST to `/config/apps/http/servers/srv0/routes` when it does not, then verify.
- **Unwire** — `GET /id/route_<app>`; 404 is a no-op success, otherwise DELETE.

Since 306 the route's `handle` list is built from `wiring_identity_mode`, so a forward_auth
app gets the auth chain and every other mode keeps the plain handler unchanged.

## Remaining

Wiring runs on every bootstrap; unwire needs a removal run.

- [ ] Unwiring removes the route and the domain returns Caddy's default response
- [ ] Unwiring a non-existent route succeeds
- [x] Wiring a fresh app produces a working HTTPS route — every bootstrap exercises this
- [x] Re-running wire is a no-op
- [x] Both tasks are gated on the provider check and no-op for a different provider

## Links

- `ansible/tasks/wiring/caddy.yml`, `ansible/tasks/unwiring/caddy.yml`
