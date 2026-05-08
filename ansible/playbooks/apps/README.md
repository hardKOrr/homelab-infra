# Adding a New App

Five steps. Most of the work is step 3.

---

## Step 1 — Decide the hosting type

| Type | Use when | Examples |
|---|---|---|
| **Docker on LXC** | App distributes a Docker image; multi-container stacks | Sonarr, Radarr, Jellyfin |
| **Native LXC** | Single binary or apt-installable; no Docker needed | Vaultwarden, Caddy, Ntfy |
| **Docker on VM** | Needs full kernel (rare) | k3s |
| **VM** | Has its own installer (rare) | PBS |

When in doubt, Docker on LXC is the safe default for anything with a Docker image.

---

## Step 2 — Copy the templates

```bash
# App defaults (resource sizes, stack, port)
cp ansible/vars/app-defaults/_template.yml ansible/vars/app-defaults/sonarr.yml

# Role (the actual deployment logic)
cp -r ansible/roles/_template-docker/ ansible/roles/sonarr/
# or for native LXC:
cp -r ansible/roles/_template-native/ ansible/roles/sonarr/

# App playbook (entry point)
cp ansible/playbooks/apps/_template.yml ansible/playbooks/apps/sonarr.yml

# User-facing config example
cp config.example/apps/_template.example.yml config.example/apps/sonarr.example.yml
```

Do a find-and-replace of `APP_NAME` → `sonarr` across all four files.

---

## Step 3 — Fill in the blanks

**`vars/app-defaults/sonarr.yml`**
- Set `cores`, `memory` to realistic values for this app
- Set `stack: media_stack` (Docker) or fill in the `proxmox:` block (native LXC)
- Set `app.port` to the app's default port
- Set `routing.auth: false` if the app has its own login (no need for Authentik in front)
- For native LXC binaries from GitHub: uncomment the `update.github_repo` key

**`roles/sonarr/tasks/main.yml`**
- Docker: point the image at the correct registry/tag, set environment variables, volumes
- Native: choose apt or binary install path, write the config template, set the service name
- Adjust the health check URL to one that actually returns 200 when the app is ready

**`roles/sonarr/templates/docker-compose.yml.j2`** (Docker only)
- Set the correct image name and tag
- Add environment variables the app needs
- Add volumes for config and data paths
- Add any peer services the app talks to internally (e.g. a database sidecar)

**`playbooks/apps/sonarr.yml`**
- In Play 1: uncomment PATH A (Docker) or PATH B (native LXC) and delete the other
- In Play 2: change `hosts:` to match your hosting type
  - Docker: `hosts: tag_media_stack` (replace with your stack tag)
  - Native LXC: `hosts: app_deploy`
- In Play 3 (Wire): add any app-to-app wiring in the commented section at the bottom

**`config.example/apps/sonarr.example.yml`**
- Expose only the knobs a user might legitimately want to change
- Document each one with a comment explaining what changing it does
- Do NOT expose internal role variables — only app-facing config

---

## Step 4 — Add to Wire Stack (if Docker app)

If the app needs to communicate with other apps on its stack (e.g. Sonarr → Prowlarr, Sonarr → qBittorrent), add it to the stack's wire playbook:

```bash
# Edit the relevant stack wire playbook:
ansible/playbooks/stacks/wire-media-stack.yml
```

Add a task that connects this app to its peers via the app's API. All tasks in the wire playbook must be idempotent (check-before-create).

---

## Step 5 — Test

```bash
cd ansible/

# Dry run (check mode — no changes made)
ansible-playbook -i inventory/ playbooks/apps/sonarr.yml -e instance=sonarr --check

# Real deploy
ansible-playbook -i inventory/ playbooks/apps/sonarr.yml -e instance=sonarr

# Verify wiring
ansible-playbook -i inventory/ playbooks/stacks/wire-media-stack.yml
```

Check:
- [ ] App is accessible at `https://sonarr.yourdomain.com`
- [ ] Caddy/Nginx route exists
- [ ] Authentik proxy appears (if `routing.auth: true`)
- [ ] Uptime Kuma monitor is registered
- [ ] Re-running the deploy playbook makes no unwanted changes (idempotency)
- [ ] Running remove.yml tears everything down cleanly

---

## Wiring Contract Reference

Play 3 of every app playbook sets these variables before calling wiring tasks.
**Do not change the variable names** — all wiring tasks depend on them.

| Variable | Value | Used by |
|---|---|---|
| `wiring_app_name` | `{{ instance }}` | All wiring tasks — used as slug/ID |
| `wiring_upstream_host` | App container IP | Caddy, Nginx |
| `wiring_upstream_port` | App listen port | Caddy, Nginx |
| `wiring_domain` | `instance.yourdomain.com` | Caddy, Nginx, Authentik, DNS |
| `wiring_app_display` | Human label | Authentik, Uptime Kuma |
| `wiring_monitor_url` | Public HTTPS URL | Uptime Kuma |
| `wiring_auth_group` | Authentik group name | Authentik |

---

## PR Checklist

- [ ] `vars/app-defaults/<app>.yml` — sensible defaults, all keys documented
- [ ] `roles/<app>/` — idempotent, health check included, no hardcoded values
- [ ] `playbooks/apps/<app>.yml` — three-play pattern, correct hosts target
- [ ] `config.example/apps/<app>.example.yml` — user-facing knobs only
- [ ] App-to-app wiring added to relevant `stacks/wire-<stack>.yml`
- [ ] Re-run is idempotent (no spurious changes on second run)
- [ ] `remove.yml` tears down cleanly (test it)
