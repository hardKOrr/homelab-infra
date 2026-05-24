# 302 — Authentik wire + unwire

**Status:** open
**Depends on:** 200, 403 (authentik app must exist before this can be tested)
**Blocks:** SSO across all apps

## Problem

Both `tasks/wiring/authentik.yml` and `tasks/unwiring/authentik.yml` are TODO headers. Without this, every app skips SSO.

## Files

- `ansible/tasks/wiring/authentik.yml` — implement
- `ansible/tasks/unwiring/authentik.yml` — implement

## Approach

Authentik REST API at `<host>/api/v3/`. Token from `homelabinfra_infra.authentik.api_token`.

**Wire (forward auth / proxy provider mode):**
1. Find or create the homelab outpost (cache by name).
2. Find existing provider by slug — GET `/providers/proxy/?name=<wiring_app_name>`.
3. POST or PATCH provider:
   - mode=forward_single (single domain)
   - external_host=`wiring_external_url`
   - internal_host=`wiring_upstream_url`
   - authorization_flow + invalidation_flow = default homelab flows
4. Find or create application — GET `/core/applications/?slug=<wiring_app_name>`. Link to provider.
5. Bind policy: ensure group `wiring_auth_group` has access (PolicyBinding to group).
6. Add provider to outpost.

**Unwire:**
1. Find application by slug → DELETE.
2. Find provider by name → DELETE.
3. Outpost auto-updates.

Gated on `homelabinfra_infra.sso.provider == 'authentik'`.

## Acceptance

- [ ] Wire creates provider + application + policy binding
- [ ] Hitting the wired domain without a session redirects through Authentik
- [ ] Re-wire is idempotent
- [ ] Unwire removes app + provider; users can no longer SSO to that app
- [ ] No-op for `sso.provider != 'authentik'`
