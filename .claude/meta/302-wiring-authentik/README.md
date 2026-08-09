# 302 — Authentik wire + unwire

**Status:** built
**Subject:** Authentik / identity
**Related:** 009 (the mode contract this dispatches on), 306 (proxy enforcement), 403 (app)

## Goal

Both halves were TODO headers, so every app skipped SSO. Both drive Authentik's REST API at
`<sso.host>/api/v3/`, gated on `sso.provider == 'authentik'`.

Since 009, **`wiring/authentik.yml` dispatches on `wiring_identity_mode`** rather than a
boolean — catalog builds an Application tile and group binding; oidc builds an OAuth2
provider and returns client credentials; forward_auth builds a proxy provider
(`mode: forward_single`), Application, policy binding and outpost membership. Unwiring
removes whichever shape exists.

The second-deploy lookup defect is fixed: objects are found by slug or name before they are
created, so a re-run adopts rather than duplicates.

## Remaining

- [ ] Hitting a wired domain without a session redirects through Authentik, and after
      unwire a user can no longer SSO to that app — the browser sign-in leg, shared with
      306, 403 and 405. Needs a human at a browser
- [ ] Re-wire idempotent and unwire clean for each of the three shapes — the per-mode
      acceptance lives in 009, which needs one app deployed per mode
- [x] Wire creates provider + application + policy binding
- [x] No-op for `sso.provider != 'authentik'`

## Links

- `ansible/tasks/wiring/authentik.yml`, `ansible/tasks/unwiring/authentik.yml`
- `ansible/tasks/resolve-estate.yml` — resolves which estate's Authentik is targeted
- Authentik 2026.5.6 flow slugs and query-filter behaviour that this wiring depends on are
  recorded in agent project memory, not in this repo
