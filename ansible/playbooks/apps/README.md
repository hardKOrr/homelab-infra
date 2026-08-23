# Adding a New App

Six steps. Most of the work is step 4.

---

## Step 1 — Decide the hosting type

| Type | Use when | Examples |
|---|---|---|
| **Docker on LXC** | App distributes a Docker image; multi-container stacks | Sonarr, Radarr, Jellyfin |
| **Native LXC** | Single binary or apt-installable; no Docker needed | Vaultwarden, Caddy, Ntfy |
| **Docker on VM** | Needs full kernel (rare) | Home Assistant (USB passthrough) |
| **VM** | Has its own installer (rare) | PBS |
| **Kubernetes** | The app is an ordinary OCI workload and wants orchestration | Mixpost — see below |

When in doubt, Docker on LXC is the safe default for anything with a Docker image.

**The Kubernetes row is not a promotion.** `playbooks/apps/k3s-cluster.yml` builds a k3s
cluster as a hosting backend, and an app deployed onto it gains scheduling and restart
behavior it does not get from a compose file — but it also inherits the cluster's storage
contract, and the default StorageClass is node-pinned: a pod whose volume lives on an
unavailable node stays Pending rather than moving. Prefer it for stateless or
easily-restored workloads. A working Docker app has no reason to migrate. Caddy,
Vaultwarden, either Authentik estate, the runner and Proxmox/PBS stay outside the cluster
by decision, not by omission — see `docs/meta/done/204-kubernetes-hosting-backend/`.

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

## Step 3 — Classify the application

Add the application to `catalog/applications.yml`:

```yaml
  sonarr:
    name: Sonarr                        # the folder name and the "Deploy Sonarr" job name
    job: deploy-sonarr.yaml
    root: Applications                  # Applications | Platform
    category: Media & Entertainment     # the broad purpose an operator recognizes
    type: Library Automation            # the application type; omit for a Platform entry
    scope: estate                       # REQUIRED. estate | lab — see below
```

`scope` says which side of the estate boundary the application sits on. Estates are
separate, so `estate` is the ordinary answer: one deployment per estate, instances named
`<app>-<estate>[-<variant>]`. `lab` means one deployment serves every estate, and it is the
deliberate exception — comment the entry with why it earns it. There is no default; an
application with no `scope` is rejected at render time.

Do not classify it by Docker, LXC, VM, Kubernetes, stack, database dependency, or another
execution detail. Add jobs that act on the lab rather than on one application to
`rundeck/job-groups.yml` instead.

The entry produces the whole folder, not just the Deploy job: `render-job.py` projects the
group `<root>/<category>/<type>/<name>` and expands every applicable day-2 template into
`<that group>/Maintenance`. Which actions apply is read from
`vars/app-defaults/<app>.yml` — an explicit `hosting:` wins, otherwise the presence of
`stack:` tells Docker from native — so **Step 4 is what decides the app's Maintenance
folder**, and nothing here has to list its jobs.

Two optional fields:

- `extra: [migrate]` opts into an action no hosting kind selects. Only the Servarr family
  uses it today.
- `essential: true` withholds the Remove job, for a service the platform cannot run
  without.

`render-job.py --check` rejects an unclassified job, a source `group:` that has drifted from
its classification, a template no application selects, and any UUID collision.

---

## Step 4 — Fill in the blanks

**`vars/app-defaults/sonarr.yml`**
- Set `cores`, `memory` to realistic values for this app
- Set `stack: media` (Docker) or fill in the `proxmox:` block (native LXC)
- Set `app.port` to the app's default port
- Pick the `routing.identity` mode: `catalog` (default — Authentik launch tile, app
  keeps its own login), `oidc` (app consumes an OAuth2 client), `forward_auth`
  (Authentik login enforced at the reverse proxy), or `none` (platform
  infrastructure only — no Authentik object)
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
- In Play 2: leave `hosts: "deploy_{{ instance }}"` alone — it is correct for both
  hosting types. Play 1 adds the guest to that per-instance group on every path
  (PATH B's `add_host` directly, PATH A via `find-or-create-host.yml`'s
  `deploy_group`). Do NOT target a shared group or a `lab_stack_<name>` pattern:
  `add_host` groups persist for a whole run and `bootstrap.yml` chains app
  playbooks with `import_playbook`, so a shared group accumulates every earlier
  app's guest and the role would run on all of them.
- In Play 3 (Wire): add any app-to-app wiring in the commented section at the bottom

**`config.example/apps/sonarr.example.yml`**
- Expose only the knobs a user might legitimately want to change
- Document each one with a comment explaining what changing it does
- Do NOT expose internal role variables — only app-facing config

---

## Step 5 — Add to Wire Stack (if Docker app)

If the app needs to communicate with other apps on its stack (e.g. Sonarr → Prowlarr, Sonarr → qBittorrent), add it to the stack's wire playbook:

```bash
# Edit the relevant stack wire playbook:
ansible/playbooks/stacks/wire-media-stack.yml
```

Add a task that connects this app to its peers via the app's API. All tasks in the wire playbook must be idempotent (check-before-create).

---

## Step 6 — Test

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
- [ ] Authentik shape matches the identity mode (tile for `catalog`, OAuth2
      provider for `oidc`, proxy provider + outpost for `forward_auth`)
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
| `wiring_subdomain` | `routing.subdomain`, default `{{ instance }}` | builds `wiring_domain` / monitor URL |
| `wiring_domain` | `subdomain.estate-domain` | Caddy, Nginx, Authentik, DNS |
| `wiring_app_display` | Human label | Authentik, Uptime Kuma |
| `wiring_monitor_url` | Public HTTPS URL | Uptime Kuma |
| `wiring_auth_group` | Authentik group name | Authentik |
| `wiring_identity_mode` | `routing.identity`, default `catalog` | Authentik (mode dispatch), proxy forward_auth (slice 306) |

Play 3 also runs `tasks/resolve-estate.yml` before wiring: it overlays the app's
`routing.estate` domain/sso/dns facts onto `homelabinfra_infra`, so wiring tasks
stay estate-agnostic.

---

## PR Checklist

- [ ] `vars/app-defaults/<app>.yml` — sensible defaults, all keys documented
- [ ] `roles/<app>/` — idempotent, health check included, no hardcoded values
- [ ] `playbooks/apps/<app>.yml` — three-play pattern, correct hosts target
- [ ] `config.example/apps/<app>.example.yml` — user-facing knobs only
- [ ] `catalog/applications.yml` — purpose/type classification; it also generates the app's Maintenance folder
- [ ] `rundeck/jobs/deploy-<app>.yaml` — stable UUID and projected catalog group
- [ ] App-to-app wiring added to relevant `stacks/wire-<stack>.yml`
- [ ] Re-run is idempotent (no spurious changes on second run)
- [ ] `remove.yml` tears down cleanly (test it)
