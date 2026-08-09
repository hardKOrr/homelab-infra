# 400 — notes

## Superseded planning text (moved from README, 2026-08-08)

The original install approach, kept for provenance. It does **not** describe what shipped:
upstream ships no GitHub binary assets, so the install mechanism deviates. The admin-token
console-print in step 5 was superseded by slice 013 (durable sink), and the `routing.auth`
key by slice 017 (`routing.identity`).

- `ansible/roles/vaultwarden/handlers/main.yml`
- `ansible/roles/vaultwarden/defaults/main.yml`
- `ansible/roles/vaultwarden/meta/main.yml`
- `ansible/roles/vaultwarden/templates/config.j2` (or env file)
- `ansible/roles/vaultwarden/files/lab-update-check`
- `ansible/roles/vaultwarden/files/lab-restart-app`
- `ansible/roles/vaultwarden/files/lab-tail-applog`
- `ansible/playbooks/apps/vaultwarden.yml` (three-play pattern, PATH B native LXC)
- `config.example/apps/vaultwarden.example.yml`

Existing:
- `ansible/vars/app-defaults/vaultwarden.yml` — already declares defaults, may need adjustment

## Approach

Native LXC install via binary release from `dani-garcia/vaultwarden`:
1. Install runtime deps (libssl, libpq, libsqlite3 etc.).
2. Fetch latest binary from GitHub releases, place at `/usr/local/bin/vaultwarden`.
3. Fetch web-vault zip from `dani-garcia/bw_web_builds`, extract to `/usr/share/vaultwarden/web-vault`.
4. Create `vaultwarden` system user.
5. Template environment file at `/etc/vaultwarden.env` with:
   - DATA_FOLDER=/opt/vaultwarden/data
   - ADMIN_TOKEN=<argon2 hash; on first bootstrap, generate a random token and **print it to console** so the user can save it>
   - ROCKET_PORT={{ app_config.app.port }}
   - WEB_VAULT_FOLDER=/usr/share/vaultwarden/web-vault
   - DOMAIN=https://vaultwarden.{{ homelabinfra_infra.domain }} (set after wiring, may need a second pass)
6. systemd unit pointing at the binary + env file.
7. Health check `GET /alive`.
8. Implement the three `lab-*` scripts (update-check uses GitHub releases API).

Play 3 wires Caddy + Uptime Kuma + DNS — **skips Authentik** (`routing.auth: false` in defaults).

Bootstrap chicken-and-egg: on first deploy, admin token is generated locally and printed; user pastes into `config/infrastructure.yml` (per CLAUDE.md). On subsequent deploys, the token comes from there.

