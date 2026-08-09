# 009 — Identity-mode contract (routing.identity)

**Status:** built
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

Live acceptance needs one app deployed per mode.

- [ ] catalog deploy creates Application (provider: null) + binding, no provider, no outpost
      change; re-run reports no change
- [ ] oidc deploy creates OAuth2 provider + Application and exports client creds to the play
- [ ] forward_auth path produces byte-identical objects to the pre-rework wiring
- [ ] `remove.yml` unwires each shape cleanly, including after a mode change
- [ ] `routing.identity: none` deploy touches Authentik not at all

## Links

- `ansible/tasks/wiring/authentik.yml` — mode dispatch (the 302 rework);
  `ansible/tasks/unwiring/authentik.yml` removes whichever shape exists
- `ansible/vars/app-defaults/*.yml` + `_template.yml` — `identity` replaces `auth`
- `ansible/playbooks/apps/*.yml` + `_template.yml` — the `wiring_identity_mode` var; the
  Wire SSO skip-condition is `wiring_identity_mode != 'none'`
- `ansible/vars/CONTRACT.md` app-layering note, `config.example/apps/*`
- `ansible/playbooks/apps/README.md` — checklist and wiring-contract table
