# 009 — Identity-mode contract (routing.identity)

**Status:** in-progress — implementation complete; awaiting gate + live acceptance
**Depends on:** 008 (estate contract — modes are estate-aware by construction), 302, 403
**Blocks:** 306 (forward_auth enforcement gates on the mode)

## Problem

`routing.auth` was a boolean: true meant "proxy provider + outpost" (enforced by
nothing until slice 306), false meant "no Authentik object at all". Reality has
four shapes: apps that want a portal tile but keep their own login, apps that
consume OIDC, apps that want proxy-enforced login, and platform infrastructure
that must never appear in Authentik.

## Contract

`routing.identity: none | catalog | oidc | forward_auth`, default **catalog**.

- `none` — no Authentik object (Authentik itself, Caddy)
- `catalog` — Application tile + group binding only; the app keeps its own login
  (all current baseline apps: Vaultwarden, Ntfy, Uptime Kuma, observability, PBS)
- `oidc` — OAuth2 provider + Application; `client_id`/`client_secret` handed back
  to the caller as `authentik_oidc_client_id/_secret` facts (not recorded in the
  registry; the first consuming app slice settles durable storage per the secrets
  model)
- `forward_auth` — proxy provider + Application + outpost membership; proxy-side
  enforcement is slice 306

`routing.auth` is superseded; nothing reads it.

## Files

- `ansible/vars/app-defaults/*.yml` + `_template.yml` — `identity` replaces `auth`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — `wiring_identity_mode` wiring
  var; Wire SSO skip-condition becomes `wiring_identity_mode != 'none'`
- `ansible/tasks/wiring/authentik.yml` — mode dispatch (302 rework)
- `ansible/tasks/unwiring/authentik.yml` — removes whichever shape exists
- `ansible/vars/CONTRACT.md` app-layering note; `config.example/apps/*`
- `ansible/playbooks/apps/README.md` — checklist + wiring contract table

## Acceptance

- [ ] catalog app deploy creates Application (provider: null) + binding, no
      provider, no outpost change; re-run reports no change
- [ ] oidc app deploy creates OAuth2 provider + Application and exports client
      creds to the play
- [ ] forward_auth path produces byte-identical objects to the pre-rework wiring
- [ ] remove.yml unwires each shape cleanly, including after a mode change
- [ ] `routing.identity: none` app deploy touches Authentik not at all
