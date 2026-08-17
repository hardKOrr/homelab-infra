# 302 — Authentik wire + unwire

**Status:** closed 2026-08-16 — operator accepted the proven object lifecycle and deferred
the remaining browser matrix to the planned full Authentik session
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

- [x] Hitting a wired domain without a session redirects through Authentik — met 2026-08-13
      for the `forward_auth` shape: Sonarr redirected an unauthenticated browser to
      Authentik, akadmin signed in, and the browser returned authenticated. OIDC sign-in
      and post-unwire browser denial were not observed; the operator accepted their
      deferral to the planned full Authentik session on 2026-08-16
- [x] Re-wire idempotent and unwire clean for each of the three shapes — the per-mode
      acceptance lives in 009. **The catalog shape is done**: executions 80–88 and 91–100
      (2026-08-11/12) re-wired ten published apps, and
      querying Authentik's `policies/bindings/` afterwards found **exactly one enabled
      binding to `homelab-users` per application** — no duplicates, so the slug/name lookup
      adopts as designed. **The catalog shape's unwire is proven clean** —
      `qbittorrent-pintest`, executions
      169–171 (2026-08-16): wire created the application and one `homelab-users` binding,
      unwire deleted the application with both provider deletes correctly **skipped** (a
      catalog app owns no provider), and a second unwire was a no-op. **The OIDC object
      lifecycle is proven** by executions 160–161: creation after a catalog → OIDC mode
      change, then removal to zero applications and providers. **The forward-auth wire and
      sign-in are proven** on Sonarr. Forward-auth unwire and browser denial were not
      observed; the operator accepted that deferral on 2026-08-16
- [x] Wire creates provider + application + policy binding
- [x] No-op for `sso.provider != 'authentik'`

## Links

- `ansible/tasks/wiring/authentik.yml`, `ansible/tasks/unwiring/authentik.yml`
- `ansible/tasks/resolve-estate.yml` — resolves which estate's Authentik is targeted
- Authentik 2026.5.6 flow slugs and query-filter behaviour that this wiring depends on are
  recorded in agent project memory, not in this repo
