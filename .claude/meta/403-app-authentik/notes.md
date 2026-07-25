# 403 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items. **One acceptance item is blocked on work this
slice does not own — see "Known gap" below. Read that before flipping to done.**

### What's in

- `roles/authentik/` — compose stack (server, worker, PostgreSQL, Redis), generated
  secrets with continuity, health gate, API-token verification.
- `playbooks/apps/authentik.yml` — PATH A Docker; records `sso` in Play 3 before wiring.
- `vars/app-defaults/authentik.yml`, `config.example/apps/authentik.example.yml`.
- `playbooks/bootstrap.yml` step 4 activated.

This is the first slice to exercise the Docker app path end to end
(`find-or-create-host.yml` → docker role → compose).

### Decision: fully scripted admin bootstrap

The README offered "print the setup URL and let the user finish interactively" OR
"auto-create the admin user". Interactive setup breaks the one-click promise, so the
role uses `AUTHENTIK_BOOTSTRAP_PASSWORD` / `AUTHENTIK_BOOTSTRAP_TOKEN` /
`AUTHENTIK_BOOTSTRAP_EMAIL`. The bootstrap token becomes the API token the wiring
tasks authenticate with, so no human step sits between deploy and a usable SSO
provider.

Caveat worth remembering: those variables are consumed **only on the very first
start**, when the default tenant is created. Changing the token later has no effect
on a running instance. The role therefore verifies the token against
`/api/v3/core/users/me/` and fails with an explicit message rather than letting the
next app deploy fail at its SSO wiring step, far from the cause.

### Decision: `sso.host` is the LAN URL, not the public domain

`http://<stack-ip>:9000`, not `https://authentik.<domain>`. During bootstrap neither
DNS nor a certificate exists for the public name yet, and the wiring tasks call this
API from the controller on every later app deploy. Using the direct address removes
both dependencies. Only the API base is affected — forward-auth `external_host` is
still the public `https://<app>.<domain>`, derived per app by `wiring/authentik.yml`.

### Decision: no Watchtower label on this stack

Every other Docker app opts into Watchtower. Authentik does not: an upgrade runs
database migrations and server and worker must move together, so an independent pull
of either risks a half-migrated cluster. Re-running the deploy is the upgrade path —
it re-resolves the newest GitHub release tag and recreates the stack. `app.image_tag`
pins a version to freeze that.

### Secret continuity

`PG_PASS`, `AUTHENTIK_SECRET_KEY` and the admin password are read back from the
existing `/opt/<instance>/.env` before anything is generated. Regenerating `PG_PASS`
would lock the server out of its own database; regenerating the secret key would
invalidate every session. The API token additionally prefers the value already in
`facts.yml`.

Guest-held credentials are unavoidable here — a containerised database and its client
both read the password — so `.env` is 0600 root-owned in a 0750 directory. Same
narrow exception to `.claude/specs/secrets-handling.md` documented in the role header.

### Contract addition

`sso` gains provider-specific optional fields `admin_user` / `admin_password`
(CONTRACT.md §3). Nothing reads them; akadmin's password is generated rather than
prompted, so without recording it the operator cannot sign in.

### Known gap — blocks acceptance item 3

Acceptance item 3 is "a test app wired via 302 redirects through Authentik
successfully". That cannot pass yet, and the cause is **not in this slice's files**:

`tasks/wiring/caddy.yml` (slice 300) writes a plain `reverse_proxy` route. Nothing
adds a `forward_auth` handler in front of it. So after this slice, an app with
`routing.auth: true` gets a correct Authentik proxy provider, application, policy
binding and outpost membership — and a Caddy route that sends traffic straight to the
upstream without ever consulting Authentik. SSO objects exist; SSO does not enforce.

Closing it means teaching `wiring/caddy.yml` to emit the forward_auth handler chain
(reverse_proxy to the outpost at `/outpost.goauthentik.io/auth/caddy`, with
`handle_response` copying the auth headers through on 2xx and copying the response on
everything else) when the app has `routing.auth: true`, plus the mirror change in
`wiring/nginx.yml`. That is slice 300/301/302 territory, it needs a live Authentik to
verify against, and it was deliberately left out of this slice rather than shipped as
unverified JSON that would look implemented while silently failing open.

**Recommendation: open a slice for it and make 302 depend on it.** Do not mark 302 or
403 done until an app actually redirects through Authentik.

### Verification

- ansible-lint: clean (production profile).
- syntax-check: `playbooks/apps/authentik.yml` and `playbooks/bootstrap.yml` clean.
  Repo-wide, only the pre-existing slice-502 stub fails.
- NOT verified live.

### Live acceptance TODO

- Authentik UI loads at the wired domain; akadmin signs in with `sso.admin_password`.
- `facts.yml` `sso` block has a token that authenticates.
- Re-run is idempotent: no secret rotation, no container recreate churn.
- The stack host has enough RAM (4 GB is a comfortable floor for four containers).
- Acceptance item 3 — blocked, see "Known gap".

## 2026-07-25 — account/hostname doctrine (with slices 008/009)

- Bootstrap admin renamed post-first-start: AUTHENTIK_BOOTSTRAP_* always creates
  akadmin, so the role PATCHes the username to `collector` after the whoami
  verify; the assert now accepts either name so pre-doctrine labs re-run clean.
  Password and API token survive the rename.
- Canonical hostname is `auth.<domain>`: `routing.subdomain: auth` in
  app-defaults; Play 3 computes wiring_domain from wiring_subdomain. Optional
  `routing.wire_instance_alias: true` keeps `<instance>.<domain>` routed during
  migration.
- Standing groups (homelab-users, homelab-admins/is_superuser) created at deploy
  time — the wiring's "binding lands on next wire" fallback is now the exception.
- Optional Google OAuth source configured only when both
  `app.google_client_id/_secret` are set (email_link matching). MFA enrollment
  recorded as an operator step in the new roles/authentik/README.md.
- SSO facts write is estate-scoped via `generated_facts_estate` — a
  `routing.estate` deploy records `estates.<name>.sso` for that estate only.
