# 306 — Reverse-proxy forward_auth handler for forward-auth-mode apps

**Status:** done — the Caddy path (the only reachable path in this lab) is fully verified
live. The Nginx path is out of scope here for the same reason 301 is parked in
`no-target/`: this lab runs no NPM instance to verify against.
**Subject:** Authentik / identity
**Related:** 300 + 301 (proxy wiring), 302 (Authentik wiring), 403 (a live Authentik), 009

## Goal

An app with `routing.identity: forward_auth` got a complete Authentik side — proxy provider
`forward_single`, application, group policy binding, outpost membership — and a Caddy route
that was a plain `reverse_proxy` handler. Nothing put Authentik in front of it. Traffic went
straight to the upstream.

That is worse than an obvious failure: every object looked correct in the Authentik UI, the
deploy reported success, and **the app was wide open. Forward-auth failed open, silently.**

`wiring/caddy.yml` now builds the route's `handle` list from `wiring_identity_mode` — the
forward_auth chain (outpost path route → forward_auth probe with guarded per-header copy →
app reverse_proxy) for forward_auth apps backed by an Authentik, and the unchanged plain
handler for everything else, so the drift comparison does not churn catalog/oidc/none apps.
`wiring/nginx.yml` does the same through NPM's `advanced_config`. **Both assert that
`sso.host` is known before publishing a forward_auth route, so the mode can no longer fail
open.** The unwiring pair needed no change — both delete the whole object.

The header copy is guarded per header deliberately: an unguarded `headers` handler forwards
absent values as literal `{http.reverse_proxy.header.…}` placeholder text.

This gates the `forward_auth` mode only. Catalog promises no enforcement and oidc enforces
inside the app; no baseline app defaults to forward_auth, so the gap goes live the first
time one opts in.

## Remaining

- [x] Full interactive sign-in leg — browser login, then back to the app — met 2026-08-13:
      Sonarr (`routing.identity: forward_auth`) redirected an unauthenticated browser to
      Authentik's login flow, akadmin signed in, and the browser returned to Sonarr
      authenticated
- [x] Nginx path reaches the same outcomes — **not reachable in this lab**: no NPM instance
      exists to verify against, the same carve-out that parks 301 in `no-target/`. Not
      counted against closing this slice; flip back to open if an NPM app slice ever ships
- [x] A `forward_auth` app redirects to the Authentik login flow when unauthenticated —
      `302` to `/application/o/authorize/?client_id=…` from the real outpost
- [x] The 2xx branch copies identity onto the upstream request, omitting absent headers
- [x] An app in any other identity mode is unaffected — a catalog app's route is
      byte-identical to what 300 produced
- [x] Re-running against an already-protected app changes nothing — the drift comparison is
      stable across the nested handler chain
- [x] A drifted route (outpost address changed) is PATCHed in place, and the next run is
      clean
- [x] Unwiring removes the route including the auth handlers, and is idempotent
- [x] A denied user is denied — a non-2xx from the outpost is copied to the client
      untouched (`403` reached the browser, upstream never dialed)

Verified 2026-07-25 by driving `tasks/wiring/caddy.yml` against a clean-room Caddy (admin
`:2020`, server `:8082`) inside the live Caddy LXC — Caddy 2.10.2, Authentik 2026.5.6 — with
a real proxy provider, application and embedded outpost membership. All test objects removed
afterwards; production `srv0` was never touched.

## Links

- `ansible/tasks/wiring/caddy.yml`, `ansible/tasks/unwiring/caddy.yml`
- `ansible/tasks/wiring/nginx.yml`, `ansible/tasks/unwiring/nginx.yml`
- `ansible/vars/CONTRACT.md` — the proxy wiring reads `wiring_identity_mode`, which every
  app playbook's Play 3 already sets from `app_config.routing.identity`. No playbook
  changes were needed
- `ansible/tasks/resolve-estate.yml` — resolves `homelabinfra_infra.sso` per estate before
  the wire runs, which is how the outpost is found
