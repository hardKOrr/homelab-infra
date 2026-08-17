# 302 — notes

2026-07-25 — implementation complete; slice stays in-progress until live verification
against a deployed Authentik (slice 403).

What's in:

- `tasks/wiring/authentik.yml` — resolves the authorization and invalidation flows by
  slug, finds-or-creates the proxy provider (`mode: forward_single`), finds-or-creates
  the application (slug = `wiring_app_name`, linked to the provider pk), binds the
  access group via a policy binding, and PATCHes the outpost's `providers` list to
  include the provider. Each step is located first and only written when absent or
  drifted.
- `tasks/unwiring/authentik.yml` — inverse order: detach the provider from the outpost,
  DELETE the application (policy bindings cascade with it), DELETE the provider, verify
  the application 404s.
- Both gated on `sso.provider | default('none') == 'authentik'`.

Decisions:

- **Contract deviation from this slice's README**: README said
  `homelabinfra_infra.authentik.api_token` (superseded service-keyed sketch).
  Implementation uses Shape B `sso.host` + `sso.token`, API base `{{ sso.host }}/api/v3`.
- **Derived URLs.** The README's contract named `wiring_external_url` /
  `wiring_upstream_url`, but no app playbook sets them — the shipped wiring contract is
  `wiring_domain` + `wiring_upstream_host` + `wiring_upstream_port`. Both are accepted:
  the explicit vars win, otherwise external defaults to `https://{{ wiring_domain }}`
  and internal to `http://{{ host }}:{{ port }}`. No caller changes needed.
- **`invalidation_flow` is mandatory** on modern Authentik proxy providers, so the file
  resolves two flow slugs, not one. Both slugs (and the outpost name) are overridable
  via `wiring_authentik_auth_flow`, `wiring_authentik_invalidation_flow`,
  `wiring_authentik_outpost` for labs that renamed the stock objects; a missing flow or
  outpost fails with a message naming the override.
- **Missing access group is a warning, not a failure.** A lab that has not created
  `homelab-users` yet gets an application with no group binding plus a console message,
  and the binding lands on the next deploy. Failing the deploy over a not-yet-created
  group would break the one-click promise for a first-run lab.
- **Outpost membership is part of wiring.** A provider that is not attached to an
  outpost authenticates nobody, so the PATCH is not optional — that is why the outpost
  lookup asserts instead of skipping.
- no_log on every authenticated request (API token); asserts print only registered
  status/pk values.

Verified: ansible-lint green (production profile). Syntax gate unchanged (only the
pre-existing slice-502 empty-playbook failure). NOT verified live — acceptance items
(provider + application + binding created, redirect through Authentik, idempotent
re-wire, unwire removes SSO) need slice 403. Flip to done after the first real wire.

## 2026-07-25 — reworked for identity modes (slice 009)

`wiring/authentik.yml` now dispatches on `wiring_identity_mode`
(`catalog | oidc | forward_auth`; default forward_auth for out-of-tree callers):

- **catalog** — Application only (`provider: null`), launch URL + group binding;
  no flows lookup, no provider, no outpost.
- **oidc** — OAuth2 provider (confidential, redirect default = regex scoped to
  the app's own domain, signing key = stock self-signed cert when present) +
  Application; client_id/secret exported to the caller as
  `authentik_oidc_client_id/_secret` (not written to the registry — the first
  consuming app slice settles durable storage).
- **forward_auth** — the original proxy-provider path, unchanged.

`unwiring/authentik.yml` deletes whichever shape exists (proxy AND oauth2
lookups both tolerate absence), so mode changes between runs leave no orphans on
removal. Estate awareness comes free: `resolve-estate.yml` swaps `sso` before
this file runs. Live acceptance still pending (needs a running Authentik).

## 2026-08-16 — closed on the observed lifecycle

The operator closed this slice without expanding it into a complete Authentik acceptance
session. The implementation has live evidence for catalog idempotency, OIDC creation and
unwiring after a catalog → OIDC mode change (executions 157–161), and forward-auth sign-in.
OIDC sign-in and browser denial after a forward-auth unwire remain unobserved. They will be
covered, if needed, during the operator's planned full Authentik session; no replacement
slice was created.
