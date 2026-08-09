# 401 — Ntfy role + playbook

**Status:** done — deployed live 2026-08-03, all four acceptance items observed 2026-08-08 including the closed-by-default policy checked through the public HTTPS origin. Decisions and deviations from the approach below are in notes.md.
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

All four observed 2026-08-08 (execution 31 unless noted).

- [x] An authenticated publish produces a notification — the role's own
      "Verify authenticated publish succeeds" passes every run, and Ntfy is the sink the
      whole bootstrap notifies through: 17 messages covering the full sequence
- [x] Unauthenticated POST is denied — verified twice, from inside by the role's
      "Verify anonymous publish is denied", and **through the public HTTPS origin**:
      `POST https://ntfy.wasitacatisaw.cc/homelab` with no credentials returns **403**,
      as does an anonymous read of the topic
- [x] facts.yml has the `notifications` block populated — `host`, `instance`,
      `provider`, `topic`. **The criterion was written before 014**: the user, password
      and token it implied are deliberately not there, they live in the vault under
      `homelab-infra/notifications`, and facts.yml is now secret-free by design
- [x] Re-run is idempotent (no duplicate users) — `changed=0` for the whole ntfy host,
      with "Create publish account" and the password reset both skipping
