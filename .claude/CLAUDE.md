# homelab-infra

Ansible-based homelab automation platform. Goal: one click in Semaphore deploys a fully configured, cross-wired application on Proxmox. Designed to be shared — others clone, fill in two config files, run bootstrap, and have a working lab.

## Philosophy

- **Fire-and-forget provisioning** — create correct once, not drift enforcement. We do not police drift.
- **Defaults cover 80% of homelabs** — users only configure what differs.
- **homelab-infra manages what it creates** — no "bring your own host" support. Existing untagged resources are ignored entirely.
- **We configure tools, we do not replicate them** — Watchtower handles container updates, unattended-upgrades handles OS updates, PBS handles backups. We configure these at deploy time, not build our own.
- **No Ansible Vault** — two secrets live in gitignored `config/` files or Semaphore env vars. All others live in Vaultwarden.
- **One click per app in Semaphore** — not "deploy media stack with checkboxes". Each app is a separate job.

## Repository Structure

```
ansible/
  playbooks/
    bootstrap.yml                  # one-time platform setup
    apps/<app>.yml                 # one playbook per deployable app
    proxmox/
      create-lxc.yml
      create-vm.yml
    docker/
      create-docker-host.yml
  tasks/
    load-user-vars.yml
    network/generate-ip.yml
    proxmox/
      lxc-create.yml
      vm-create.yml
      ip-to-vmid.yml
    wiring/                        # cross-service registration tasks (conditional on provider)
      caddy.yml
      nginx.yml
      authentik.yml
      uptime-kuma.yml
      opnsense.yml
      pihole.yml
    guest-bootstrap.yml            # post-provisioning: SSH keys, unattended-upgrades, Watchtower
    bootstrap/
      write-generated-facts.yml   # writes config/.generated/facts.yml after each baseline service
      configure-pbs.yml
      configure-watchtower.yml
      configure-unattended-upgrades.yml
  roles/
    docker/                        # installs Docker Engine (Debian only)
    <app>/                         # one role per deployable app
  vars/
    homelabinfra-defaults.yml      # global defaults (git-managed)
    app-defaults/<app>.yml         # per-app sensible defaults (git-managed)
  inventory/
    proxmox.yml

config/                            # GITIGNORED — never overwritten by git pull
  proxmox.yml                      # Proxmox connection + API token
  infrastructure.yml               # platform service role declarations
  .generated/
    facts.yml                      # written by bootstrap: service endpoints + tokens
  apps/
    <instance>.yml                 # one per app instance (persists after removal = restore point)

config.example/                    # in git — fully documented templates for users to copy
  proxmox.yml
  infrastructure.yml
  apps/<app>.example.yml
```

## Config Hierarchy

Three layers merged via `combine(recursive=True)` at playbook runtime:

1. `vars/homelabinfra-defaults.yml` — global defaults
2. `vars/app-defaults/<app>.yml` — per-app defaults (cores, RAM, ports, stack assignment)
3. `config/apps/<instance>.yml` — user overrides for this instance only

Users only write what differs. Everything else falls through.

## Variable Namespaces

- `homelabinfra_config.*` — merged user + default config (input layer)
- `homelabinfra_instance.*` — computed execution-time facts (built by task files)
- `homelabinfra_infra.*` — infrastructure service facts (loaded from `config/.generated/facts.yml`)

**CRITICAL**: Always use `combine(recursive=True)` when setting keys on any of these dicts.
Never `set_fact: homelabinfra_instance: {key: val}` — it destroys all sibling keys.

## Hosting Types

| Type | Use when | Examples |
|---|---|---|
| Native LXC | Single-binary or package-installed services | Pihole, Caddy, Vaultwarden |
| Docker on LXC | Multi-container stacks | Authentik, media stack, monitoring |
| Docker on VM | Needs full kernel | k3s, kernel module deps |
| VM | Needs own installer or full OS | PBS |

No OCI container support — PVE 9.1 OCI is tech preview and not mature enough to build on.

## Stack Model

Related Docker apps group onto shared hosts ("stacks"). Stack assignment is declared in `vars/app-defaults/<app>.yml` and overridable per-instance in `config/apps/<instance>.yml`. Stack host is created on first app deploy targeting it, then reused for subsequent apps on that stack. Proxmox tags identify stacks: `tag_media_stack`, `tag_services_stack`, etc.

## Wiring Step

Every app deployment ends by registering with platform services. Each task is conditional on the configured provider — missing providers are no-ops, not errors:

1. Caddy or Nginx route (if `infrastructure.reverse_proxy.provider != none`)
2. Authentik provider (if `infrastructure.sso.provider: authentik`)
3. Uptime Kuma monitor (if Uptime Kuma instance is reachable)
4. DNS record (if `infrastructure.dns.provider != none`)

Wiring tasks read service connection details from `config/.generated/facts.yml`.

## Baseline Apps (Bootstrap Order)

`playbooks/bootstrap.yml` deploys in order — each writes its facts before the next needs them:

1. **Vaultwarden** — enforced, all platform secrets live here after bootstrap
2. **Ntfy** — notification hub (Watchtower, unattended-upgrades, Uptime Kuma all report here)
3. **Caddy or Nginx** — reverse proxy per `infrastructure.yml`
4. **Authentik** — SSO, optional per `infrastructure.yml`
5. **Uptime Kuma** — uptime monitoring, auto-registers all subsequent app deploys
6. **Prometheus + Grafana** — metrics and dashboards
7. **PBS** — backup, schedule configured by Ansible, runs autonomously

## Day-2 Operations

| Concern | Tool | Our responsibility |
|---|---|---|
| Container updates | Watchtower | Configure at Docker host creation with Ntfy endpoint |
| OS updates | unattended-upgrades | Configure in `guest-bootstrap.yml` |
| Backups | PBS | Configure schedule + datastore in bootstrap |
| Uptime alerts | Uptime Kuma | Auto-register each app URL at deploy time |

## Secrets

Two secrets outside Vaultwarden (gitignored `config/` or Semaphore env vars):
- `PROXMOX_API_TOKEN` — Proxmox connection
- `VAULTWARDEN_ADMIN_TOKEN` — written to `config/infrastructure.yml` after bootstrap step 1

Everything else: auto-generated by bootstrap, stored in Vaultwarden, retrieved via `community.general.bitwarden` lookup.

Bootstrap chicken-and-egg: Vaultwarden deploys first with no prior secrets. Admin token printed to console. User stores it. Bootstrap continues.

## Infrastructure Config (`config/infrastructure.yml`)

Declares *roles and provider choices*, not connection details. IPs and tokens go in `.generated/facts.yml`.

```yaml
reverse_proxy:
  provider: caddy       # caddy | nginx | none
  instance: caddy       # Proxmox hostname — resolved via dynamic inventory

sso:
  provider: authentik   # authentik | none
  instance: authentik

notifications:
  provider: ntfy        # ntfy | gotify | discord | none
  instance: ntfy

dns:
  provider: opnsense    # pihole | adguard | opnsense | none
  host: 192.168.1.1     # external hosts need explicit IP (not in Proxmox inventory)

backups:
  datastore_path: /mnt/backup
```

## Dynamic Inventory

`community.proxmox` plugin → groups: `proxmox_nodes`, `proxmox_clients`, `tag_<tagname>`.
All resources created by this system are tagged `homelab-infra`. Existing untagged resources are never touched.

## Subagents

- `deployment-architect` — app hosting decisions, playbook/role structure, cross-service planning
- `ansible-expert` — Ansible code, variable conventions, task calling patterns, pitfalls
- `notes-manager` — documentation, README updates, keeping docs in sync with code
- `test-developer` — Molecule, ansible-lint, integration test structure
- `project-manager` — 1-click compliance reviews, scope evaluation
