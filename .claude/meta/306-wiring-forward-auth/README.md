# 306 — Reverse-proxy forward_auth handler for forward-auth-mode apps

**Status:** built — Caddy path verified live 2026-07-25 against Caddy 2.10.2 (LXC 7020)
and Authentik 2026.5.6 (LXC 3002). Nginx path implemented, unverified (no NPM lab).
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

## Outcome

`wiring/caddy.yml` builds the route's `handle` list from `wiring_identity_mode`: the
forward_auth chain (outpost path route → forward_auth probe with guarded header copy →
app reverse_proxy) for `forward_auth` apps backed by an Authentik, the unchanged plain
`reverse_proxy` handler for everything else. `wiring/nginx.yml` does the same through
NPM's `advanced_config`. Both assert that `sso.host` is known before publishing a
forward_auth route, so the mode can no longer fail open. The unwiring pair needed no
change — both delete the whole object.

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

Verified by driving `tasks/wiring/caddy.yml` with `ansible-playbook` against a
clean-room Caddy instance (admin `:2020`, server `:8082`) inside the live Caddy LXC,
with a real Authentik proxy provider (`forward_single`) + application + embedded
outpost membership created for the test host. All test objects removed afterwards;
production `srv0` was never touched.

- [x] A `forward_auth` app redirects to the Authentik login flow when unauthenticated —
      `302` to `/application/o/authorize/?client_id=…` from the real outpost
- [x] The 2xx branch copies identity onto the upstream request — only the headers the
      outpost actually returned arrive; absent ones are omitted, not forwarded as
      literal `{http.reverse_proxy.header.…}` placeholder text (this is why the copy
      is guarded per header rather than one `headers` handler)
- [x] An app in any other identity mode is unaffected — a `catalog` app's route is
      byte-identical to what slice 300 produced:
      `{"@id":"route_x","handle":[{"handler":"reverse_proxy","upstreams":[…]}],…}`
- [x] Re-running the wire against an already-protected app changes nothing — second
      run `changed=0`; Caddy returns the stored route identical to the posted one, so
      the drift comparison is stable across the nested handler chain
- [x] A drifted route (outpost address changed) is PATCHed in place — `changed=1`, and
      the next run is clean again
- [x] Unwiring removes the route including the auth handlers, and is idempotent —
      route list back to `[]`, second unwire `changed=0`
- [x] A denied user is denied — a non-2xx from the outpost is copied to the client
      untouched (`403` reached the browser, upstream never dialed)
- [ ] Full interactive sign-in leg (browser login → back to app) — the redirect to the
      flow is verified; completing the flow needs a browser, since Authentik's flow
      executor rejects scripted POSTs without a CSRF token from the SPA. The
      post-login branch was exercised with an outpost stub returning 2xx + identity
      headers.
- [ ] Nginx path reaches the same outcomes — implemented from Authentik's documented
      nginx snippet, unverified: this lab runs no NPM (same carve-out as slice 301)
