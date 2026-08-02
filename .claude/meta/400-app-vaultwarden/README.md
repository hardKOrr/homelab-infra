# 400 — Vaultwarden role + playbook

**Status:** built — deployed live and convergent 2026-08-01. The public-domain path and
three maintenance commands still need explicit observation. Install mechanism deviates from
the original approach below because upstream ships no GitHub binary assets.
**Depends on:** 004 (proxmox keys), 005 (instance config), 200 (write-generated-facts); transitively 000-003 foundation
**Blocks:** 500 (bootstrap), every secret-using slice (anything that does a `community.general.bitwarden` lookup)

## Problem

Vaultwarden is the enforced first baseline app — it stores all platform secrets. No role and no app playbook exist yet, only `vars/app-defaults/vaultwarden.yml`.

Native LXC deployment. Has its own auth (no Authentik in front).

## Files

To create:
- `ansible/roles/vaultwarden/tasks/main.yml`
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

## Live result (2026-08-01)

The fresh runner workflow created the tagged Vaultwarden LXC at 192.168.0.10, installed
Vaultwarden 1.37.1, returned HTTP 200, and installed the three `lab-*` commands. The admin
token was generated into the slice-013 sink in one pass and did not appear in the log.
`Bootstrap Platform` then reconciled the same guest repeatedly; by execution 11 the
Vaultwarden play reported `changed=0`. Caddy has the route and correct upstream, but the
public hostname was not exercised from a browser/client, and installation alone is not an
execution test of the maintenance commands.

## Acceptance

- [ ] Fresh deploy creates LXC, installs Vaultwarden, web vault loads at the wired domain
      — LXC/install/direct HTTP and Caddy route observed; public hostname still unobserved
- [x] First deploy preserves the admin token exactly once without exposing it in the log
      — slice 013 superseded the original console-print mechanism with a durable sink
- [x] Subsequent re-runs are idempotent
- [ ] `lab-update-check` reports installed vs latest correctly
- [ ] `lab-restart-app` restarts the service
- [ ] `lab-tail-applog` shows journalctl output
- [ ] Remove playbook stops + unwires cleanly (covered by slice 501)
