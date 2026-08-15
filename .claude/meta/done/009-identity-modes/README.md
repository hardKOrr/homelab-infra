# 009 — Identity-mode contract (routing.identity)

**Status:** closed 2026-08-15 — every mode exercised live in the foxglove estate's own
Authentik (executions 156–161), except a comparison against code that no longer exists.
**Subject:** Authentik / identity
**Related:** 008 (modes are estate-aware by construction), 302 (wiring), 306 (enforcement)

## Goal

`routing.auth` was a boolean: true meant "proxy provider + outpost" — enforced by nothing
until 306 — and false meant "no Authentik object at all". Reality has four shapes: apps that
want a portal tile but keep their own login, apps that consume OIDC, apps that want
proxy-enforced login, and platform infrastructure that must never appear in Authentik.

**The contract:** `routing.identity: none | catalog | oidc | forward_auth`, default
**catalog**. `routing.auth` is superseded; nothing reads it.

| Mode | What wiring creates |
|---|---|
| `none` | nothing — Authentik itself, Caddy |
| `catalog` | Application tile + group binding only; the app keeps its own login. Every current baseline app: Vaultwarden, Ntfy, Uptime Kuma, observability, PBS |
| `oidc` | OAuth2 provider + Application; `client_id`/`client_secret` handed back as `authentik_oidc_client_id/_secret` facts, not recorded in the registry — the first consuming app slice settles durable storage per the secrets model |
| `forward_auth` | proxy provider + Application + outpost membership. Proxy-side enforcement is 306 |

## Remaining

**Every mode was exercised live on 2026-08-15**, in the second estate's own Authentik
(192.168.0.200) rather than the platform's, so the objects counted below could only have
come from these runs. `sabnzbd-foxglove` was deployed catalog, re-run, switched to oidc,
then removed.

- [x] catalog deploy creates Application (provider: null) + binding, no provider, no outpost
      change; re-run reports no change — **executions 157 and 158**. Read back from the API:
      one Application, `provider: null`, exactly one **enabled** binding to `homelab-users`,
      zero proxy providers, zero OAuth2 providers, and the embedded outpost's `providers`
      list empty. The re-run was `changed=0` on both hosts
- [x] oidc deploy creates OAuth2 provider + Application and exports client creds to the play
      — **execution 160**, a mode change on the same app. One OAuth2 provider named
      `sabnzbd-foxglove`, `client_type: confidential`, and the Application now carries the
      provider pk where it held `null`. Still zero proxy providers and an empty outpost.
      **The implementation is ahead of this slice's text**: `wiring/authentik.yml:312` also
      stores `oidc_client_id`/`oidc_client_secret` in `homelab-infra/apps/<app>`, so the
      "first consuming app slice settles durable storage" caveat above is already settled
- [ ] forward_auth path produces byte-identical objects to the pre-rework wiring — **not
      measurable**: the pre-rework wiring was deleted in the 2026-07-25 rework, so there is
      nothing to diff against. What matters was proven instead — 306 signed in through
      `forward_auth` end to end on 2026-08-13
- [x] `remove.yml` unwires each shape cleanly, including after a mode change — **execution
      161**, removing the app *after* catalog → oidc. The estate Authentik went to zero
      applications, zero OAuth2 providers and zero proxy providers, leaving no orphan of
      either shape
- [x] `routing.identity: none` deploy touches Authentik not at all — Authentik itself
      carries `identity: none`, and its own deploys (151, 156) created no Application in
      either instance: the estate's held nothing until the app arrived, and the platform's
      twelve are unchanged

## Links

- `ansible/tasks/wiring/authentik.yml` — mode dispatch (the 302 rework);
  `ansible/tasks/unwiring/authentik.yml` removes whichever shape exists
- `ansible/vars/app-defaults/*.yml` + `_template.yml` — `identity` replaces `auth`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — the `wiring_identity_mode` var; the
  Wire SSO skip-condition is `wiring_identity_mode != 'none'`
- `ansible/vars/CONTRACT.md` app-layering note, `config.example/apps/*`
- `ansible/playbooks/apps/README.md` — checklist and wiring-contract table
