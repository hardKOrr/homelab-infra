# 306 — Reverse-proxy forward_auth handler for forward-auth-mode apps

**Status:** open
**Depends on:** 300 (caddy wire), 301 (nginx wire), 302 (authentik wire), 403 (a running Authentik to verify against), identity-mode contract (slice 009)
**Blocks:** the `forward_auth` identity mode only — the exception mode an app opts into with `routing.identity: forward_auth`. Catalog and oidc modes enforce nothing at the proxy and are unaffected.

## Problem

An app with `routing.identity: forward_auth` gets a complete Authentik side from
`tasks/wiring/authentik.yml`: proxy provider (`mode: forward_single`), application,
group policy binding, and outpost membership. It also gets a Caddy route from
`tasks/wiring/caddy.yml`.

That route is a plain `reverse_proxy` handler. Nothing puts Authentik in front of it.
Traffic goes straight to the upstream and Authentik is never consulted.

The result is worse than an obvious failure: every object looks correct in the
Authentik UI, the deploy reports success, and the app is wide open. Forward-auth
fails open and silently.

Since the identity-mode contract landed, the default posture (`catalog`) creates a
launch tile only and promises no enforcement, and `oidc` apps enforce login inside
the app — so this gap no longer blocks "SSO enforcing anything", only the
forward_auth exception mode. No baseline app defaults to forward_auth; the gap
becomes live the first time an app opts in.

Found while implementing slice 403; deliberately not fixed there, because the change
belongs in the wiring files and needs a live Authentik to verify against rather than
a speculative JSON blob that would look implemented.

## Files

- `ansible/tasks/wiring/caddy.yml` — emit the forward_auth handler chain ahead of the
  `reverse_proxy` handler when `wiring_identity_mode == 'forward_auth'`
- `ansible/tasks/unwiring/caddy.yml` — confirm the inverse still removes the whole route
- `ansible/tasks/wiring/nginx.yml` — the NPM equivalent (advanced config / `auth_request`)
- `ansible/tasks/unwiring/nginx.yml` — inverse
- `ansible/vars/CONTRACT.md` — note that the proxy wiring reads `wiring_identity_mode`

The wiring contract variable already exists: every app playbook's Play 3 sets
`wiring_identity_mode` from `app_config.routing.identity` (identity-mode contract).
No playbook changes are needed here.

## Approach

1. In `wiring/caddy.yml`, when `wiring_identity_mode | default('') == 'forward_auth'`
   and `sso.provider == 'authentik'`, build the route's `handle` list as
   forward_auth-then-reverse_proxy:
   - a `reverse_proxy` handler to the Authentik outpost, `rewrite`n to
     `/outpost.goauthentik.io/auth/caddy`
   - `handle_response`: on 2xx, copy the `X-Authentik-*` headers onto the request; on
     everything else, `copy_response` so redirects to the login flow reach the browser
   - then the app's own `reverse_proxy` handler
   - plus a non-terminal route passing `/outpost.goauthentik.io/*` to the outpost
2. Keep the existing plain-route shape for every other identity mode — the drift
   comparison must not churn catalog/oidc/none apps.
3. Mirror for nginx.
4. Resolve the outpost from the app's estate (`homelabinfra_infra.sso` is already
   estate-resolved by `tasks/resolve-estate.yml` before the wire runs).
5. Verify against a real deployment, both directions.

## Acceptance

- [ ] The first app that opts into `routing.identity: forward_auth` redirects to the
      Authentik login flow when unauthenticated, and reaches the app after signing in
- [ ] An app in any other identity mode is unaffected — route JSON byte-identical to
      what slice 300 produces today, so re-wiring reports no change
- [ ] Re-running the wire against an already-protected app changes nothing
- [ ] Unwiring removes the route including the auth handlers
- [ ] A user outside the bound Authentik group is denied
- [ ] Nginx path reaches the same outcomes
