# 300 — notes

2026-07-25 — implementation complete; slice stays in-progress until live verification
against a deployed Caddy (slice 402).

What's in:

- `tasks/wiring/caddy.yml` — registers a route via the Caddy admin API. GET
  `/id/route_<name>` to detect existing; 404 → POST to
  `/config/apps/http/servers/srv0/routes`; 200 + content drift → PATCH `/id/...`;
  identical content → skip entirely (true idempotency, task reports ok not changed).
  Verify step GETs the route back. Route JSON: host matcher + reverse_proxy handler
  + `terminal: true`; TLS comes from Caddy automatic HTTPS on the srv0 host matcher,
  so the route carries no TLS config.
- `tasks/unwiring/caddy.yml` — GET `/id/route_<name>`; 404 → no-op success; 200 →
  DELETE; verify GET returns 404.
- Both wrapped in a block gated on
  `homelabinfra_infra.reverse_proxy.provider | default('none') == 'caddy'` — silent
  no-op for other providers or absent facts (chained-attr undefined is swallowed by
  `default()`), per docs/specs/provider-noop-wiring.md.

Contract deviation from this slice's README (deliberate): the README's approach
referenced `homelabinfra_infra.caddy.admin_api_url` — that is the superseded
service-keyed sketch (CONTRACT.md §3 "Superseded (b)"). Implementation follows
canonical Shape B: admin URL is built as
`{{ reverse_proxy.host }}:{{ reverse_proxy.port | default(2019) }}` (host carries the
scheme per the slice-200 decision). Slice 402's README still sketches writing
`caddy.admin_api_url` to facts — 402 must instead write Shape B
`reverse_proxy: {provider, instance, host, port}` with port = 2019 (admin API).

Assumption for 402: the caddy role templates a single HTTPS server named `srv0`
with an empty route table and the admin API on the LXC interface.

Verified: ansible-lint green (production profile). Syntax gate's only failure is the
pre-existing slice-502 stub (`stacks/rollback-container.yml` — empty playbook),
confirmed identical on a stashed tree. NOT yet verified live — acceptance items
(working HTTPS route, re-run no-op, unwire removes route, unwire absent route) need
a deployed Caddy. Flip to done after the first real wire through slice 402.

## 2026-07-25 — slice 402 landed

The assumption recorded above ("the caddy role templates a single HTTPS server named
`srv0` with an empty route table and the admin API on the LXC interface") is what
slice 402 implemented, via a JSON base config rather than a Caddyfile — an empty
Caddyfile adapts to *no* http servers at all, so `srv0` would not have existed and
the first wire would have 404'd.

402 also writes Shape B `reverse_proxy: {provider, instance, host, port}` with
port 2019, as this slice's notes required.

Two things 402 introduced that this file's behaviour now depends on:

- Caddy runs with `--resume`, so routes POSTed here persist across restarts. The
  caddy role never reloads (a reload re-applies the base config and would drop every
  route this file added).
- The caddy role reconciles only srv0's `listen` and the TLS automation policies,
  never `routes`.

Still open, and it is not this file's alone: **nothing emits a `forward_auth`
handler**, so an app with `routing.auth: true` gets a plain proxy route and SSO fails
open. Tracked as slice 306.
