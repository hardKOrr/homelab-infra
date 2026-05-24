# 401 — Ntfy role + playbook

**Status:** open
**Depends on:** 200, 400 (vaultwarden for any secrets we want to store)
**Blocks:** every other slice that sends a notification (Watchtower, unattended-upgrades, Uptime Kuma, etc.)

## Problem

Ntfy is bootstrap step 2 — the notification hub everything else reports to. No role or playbook exists.

Native LXC deployment.

## Files

To create:
- `ansible/roles/ntfy/{tasks,handlers,defaults,meta,templates,files}/...`
- `ansible/playbooks/apps/ntfy.yml`
- `ansible/vars/app-defaults/ntfy.yml`
- `config.example/apps/ntfy.example.yml`

## Approach

1. Install via official `.deb` from `https://github.com/binwiederhier/ntfy/releases` or apt repo.
2. Template `/etc/ntfy/server.yml` with:
   - base-url: `https://ntfy.{{ homelabinfra_infra.domain }}`
   - listen-http: `:{{ app_config.app.port | default(80) }}`
   - cache-file, attachment-cache-dir
   - auth-file (SQLite) and auth-default-access=deny-all (so it's not a public spam relay)
3. systemd enable + start.
4. Create a default `homelab` topic and an auth user via `ntfy user add` + `ntfy access`.
5. Persist credentials to Vaultwarden via `community.general.bitwarden` lookup write.
6. Call `write-generated-facts` with:
   ```yaml
   notifications:
     provider: ntfy
     ntfy_url: https://ntfy.{{ domain }}
     topic: homelab
     auth_user: ...
     auth_token: ...  # or from-vault lookup pointer
   ```

Wire Caddy + Uptime Kuma. Skip Authentik (Ntfy has its own auth).

Implement the three `lab-*` scripts; update-check via GitHub releases.

## Acceptance

- [ ] `curl -u user:pass -d "hello" https://ntfy.<domain>/homelab` produces a notification
- [ ] Unauthenticated POST is denied
- [ ] facts.yml has the `notifications` block populated
- [ ] Re-run is idempotent (no duplicate users)
