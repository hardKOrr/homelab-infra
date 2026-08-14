# 403 — Authentik role + playbook

**Status:** built
**Subject:** Authentik / identity
**Related:** 302 (wiring), 009 (identity modes), 306 (enforcement), 401 (Ntfy)

## Goal

Authentik is bootstrap step 4 — optional, but the default in
`config.example/infrastructure.yml`. Docker-on-LXC, and the first slice to exercise the
Docker app path.

Adapts Authentik's official compose (server, worker, postgres, redis) onto a stack host,
with the Postgres credentials and `AUTHENTIK_SECRET_KEY` generated into the vault on first
run, a readiness wait on `/-/health/ready/`, and the admin account scripted rather than left
to an interactive setup URL. It records the `sso` registry key and wires Caddy, Uptime Kuma
and DNS — but **no Authentik wiring**, because it *is* Authentik (`routing.identity: none`).

The original "known gap" — SSO objects created but not enforced, because nothing emitted a
proxy `forward_auth` handler — is closed by **306**, which builds the handler chain and
asserts `sso.host` before publishing a forward_auth route.

## Remaining

Live acceptance needs one app deployed with `routing.identity: forward_auth`, observed end
to end.

- [x] Authentik UI loads at the wired domain — met 2026-08-13, `auth.wasitacatisaw.cc`
- [x] Admin login works with credentials from the vault — met 2026-08-13, akadmin signed
      in at the admin UI
- [x] A test app wired via 302 redirects through Authentik successfully — met 2026-08-13:
      Sonarr (forward_auth) redirected through Authentik's login flow and back, once
      akadmin was added to `homelab-users` (group membership is operator content, out of
      this slice's scope — see notes.md "Known gap" resolution)
- [ ] The registry holds the API token and outpost id — recorded under `sso` in the vault,
      not `authentik.api_token` in `facts.yml` as originally written (slices 200 and 014
      moved that shape); judge against the shipped one
- [ ] Re-run is idempotent

## Links

- `ansible/roles/authentik/` — role and compose template
- `ansible/playbooks/apps/authentik.yml` (PATH A, Docker)
- `ansible/vars/app-defaults/authentik.yml`, `config.example/apps/authentik.example.yml`
- [notes.md](notes.md) — decisions, deviations, the closed known-gap write-up, and the
  superseded planning text
