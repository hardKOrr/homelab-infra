# 306 — Reverse-proxy forward_auth handler for SSO-protected apps

**Status:** open
**Depends on:** 300 (caddy wire), 301 (nginx wire), 302 (authentik wire), 403 (a running Authentik to verify against)
**Blocks:** SSO actually enforcing anything — 403 acceptance item 3, 302 acceptance

## Problem

After slices 302 and 403, an app with `routing.auth: true` gets a complete Authentik
side: proxy provider (`mode: forward_single`), application, group policy binding, and
outpost membership. It also gets a Caddy route from `tasks/wiring/caddy.yml`.

That route is a plain `reverse_proxy` handler. Nothing puts Authentik in front of it.
Traffic goes straight to the upstream and Authentik is never consulted.

The result is worse than an obvious failure: every object looks correct in the
Authentik UI, the deploy reports success, and the app is wide open. SSO fails open
and silently.

Found while implementing slice 403; deliberately not fixed there, because the change
belongs in the wiring files and needs a live Authentik to verify against rather than
a speculative JSON blob that would look implemented.

## Files

- `ansible/tasks/wiring/caddy.yml` — emit the forward_auth handler chain ahead of the
  `reverse_proxy` handler when the app is SSO-protected
- `ansible/tasks/unwiring/caddy.yml` — confirm the inverse still removes the whole route
- `ansible/tasks/wiring/nginx.yml` — the NPM equivalent (advanced config / `auth_request`)
- `ansible/tasks/unwiring/nginx.yml` — inverse
- `ansible/playbooks/apps/_template.yml` and every `apps/<app>.yml` — pass whatever new
  wiring contract variable signals "this app is SSO-protected"
- `ansible/vars/CONTRACT.md` — document the new wiring contract variable

## Approach

1. Add one wiring contract variable (e.g. `wiring_auth_enabled`), set from
   `app_config.routing.auth` in each app playbook's Play 3. Wiring tasks must not
   read app internals directly — `.claude/specs/provider-noop-wiring.md`.
2. In `wiring/caddy.yml`, when it is true and `sso.provider == 'authentik'`, build the
   route's `handle` list as forward_auth-then-reverse_proxy:
   - a `reverse_proxy` handler to the Authentik outpost, `rewrite`n to
     `/outpost.goauthentik.io/auth/caddy`
   - `handle_response`: on 2xx, copy the `X-Authentik-*` headers onto the request; on
     everything else, `copy_response` so redirects to the login flow reach the browser
   - then the app's own `reverse_proxy` handler
   - plus a non-terminal route passing `/outpost.goauthentik.io/*` to the outpost
3. Keep the existing plain-route shape for `routing.auth: false` apps — the drift
   comparison must not churn them.
4. Mirror for nginx.
5. Verify against a real deployment, both directions.

## Acceptance

- [ ] An app with `routing.auth: true` redirects to the Authentik login flow when
      unauthenticated, and reaches the app after signing in
- [ ] An app with `routing.auth: false` is unaffected — route JSON byte-identical to
      what slice 300 produces today, so re-wiring reports no change
- [ ] Re-running the wire against an already-protected app changes nothing
- [ ] Unwiring removes the route including the auth handlers
- [ ] A user outside the bound Authentik group is denied
- [ ] Nginx path reaches the same outcomes
