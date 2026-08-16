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
narrow exception to `docs/specs/secrets-handling.md` documented in the role header.

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

## 2026-07-25 — estate hostname (with slices 008/009)

- `routing.subdomain` default `auth` in app-defaults; Play 3 computes
  wiring_domain from wiring_subdomain. Multi-estate labs reach each estate's
  Authentik at that estate's `auth.<domain>`. It is a default, not enforcement —
  override it in `config/apps/<instance>.yml` like any routing key.
- SSO facts write is estate-scoped via `generated_facts_estate` — a
  `routing.estate` deploy records `estates.<name>.sso` for that estate only.

### Reverted same day — scope correction

An earlier pass added directory-content management to the role: renaming the
bootstrap admin to `collector`, creating homelab-users/homelab-admins groups, a
Google OAuth source, an MFA operator doctrine, and a `wire_instance_alias`
migration route. None of it is multi-domain work; it was lifted from the
operator's personal lab plans (`docs/meta/02-*`, `03-*`) and hard-coded as
product defaults. All removed.

**Boundary this establishes:** the role provisions the service and hands back its
endpoint, admin credentials and token. Account names, group membership, social
login sources and MFA enforcement are operator policy set in the Authentik UI. A
re-deploy must never overwrite a decision made there.

## 2026-08-14 — token proven; idempotence exposed one mode fight

Deploy Authentik executions 142 and 143 both succeeded. On both runs the vault-backed API
token authenticated and the role asserted it belongs to the bootstrap admin, closing the
registry-token acceptance item. The outpost ID from the superseded planning shape is not a
stored field: wiring resolves `authentik Embedded Outpost` by name, which the live Sonarr
forward-auth path has already exercised.

Execution 142 pulled a newer image and recreated the stack, so its two changes were a real
update rather than idempotence evidence. The immediate execution 143 then showed secrets,
the compose environment, Compose template, image state and containers unchanged. One task
still changed: PostgreSQL had restored its data directory to runtime mode `0700` during the
recreate, while the role declared `0750` and forced it back on the next deploy. Redis stayed
at `0750`.

The role now declares the database directory `0700` and Redis `0750`, without managing
container-owned uid/gid. Both repository gates pass. The final idempotence box remains open
until that revision reaches the runner's tracked branch and one deploy confirms the
directory task is unchanged.

## 2026-08-14 — idempotence proven; slice closed

Rundeck execution 144 refreshed the runner to pushed revision `8138199` and applied the
expected one-time database mode transition from the old declaration to `0700`; Redis was
already unchanged. Immediate execution 145 then reported both items `ok`. Its full recap
was `changed=0`, `unreachable=0`, `failed=0` on localhost and `sso-stack`.

That closes the final acceptance item and slice 403.

---

## Superseded planning text (moved from README, 2026-08-08)

The pre-build approach, kept for provenance. The `facts.yml` shape in step 6 was superseded
by slices 200 and 014 (the key is `sso`, and the token lives in the vault), and
`routing.auth` by slice 017 / 009 (`routing.identity`).

- `ansible/vars/app-defaults/authentik.yml` — assign to a stack (e.g. `core_stack` or its own host)
- `config.example/apps/authentik.example.yml`

## Approach

Authentik ships an official `docker-compose.yml` with server + worker + postgres + redis. Adapt it.

1. Ensure stack host exists (find-or-create-host with stack `core_stack` or `authentik_stack`).
2. Template compose file with:
   - server, worker, postgresql, redis containers
   - Volumes for media, custom-templates, certs
   - Postgres credentials generated and stored in Vaultwarden on first run
   - `AUTHENTIK_SECRET_KEY` generated and stored in Vaultwarden
3. `docker compose up -d`.
4. Wait for `/-/health/ready/` to return 200.
5. On first deploy, run the initial-setup flow URL is printed (user finishes setup interactively) — OR auto-create the admin user via the bootstrap token mechanism and store the admin password in Vaultwarden.
6. Call `write-generated-facts`:
   ```yaml
   authentik:
     api_url: https://auth.<domain>/api/v3
     api_token: <from-vault>
     outpost_id: <default-embedded-outpost-id>
   ```

Wire Caddy + Uptime Kuma + DNS. **No Authentik wiring** (it IS Authentik — `routing.auth: false`).

## Acceptance
