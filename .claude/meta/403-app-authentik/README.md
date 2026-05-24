# 403 — Authentik role + playbook

**Status:** open
**Depends on:** 302 (wiring), 401 (ntfy)
**Blocks:** SSO across all apps that set `routing.auth: true`

## Problem

Authentik is bootstrap step 4 (optional, but the default in `config.example/infrastructure.yml`). No role or playbook exists.

Docker-on-LXC deployment. First slice that exercises the Docker app path.

## Files

To create:
- `ansible/roles/authentik/{tasks,handlers,defaults,meta,templates}/...`
- `ansible/roles/authentik/templates/docker-compose.yml.j2`
- `ansible/playbooks/apps/authentik.yml` (PATH A — Docker)
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

- [ ] Authentik UI loads at the wired domain
- [ ] Admin login works with credentials from Vaultwarden
- [ ] A test app wired via 302 redirects through Authentik successfully
- [ ] facts.yml has the api_token + outpost_id
- [ ] Re-run is idempotent
