# Architecture

Ansible-based homelab automation platform: one click in Semaphore/Rundeck deploys a fully
configured, cross-wired application on Proxmox. Designed to be cloned by others — fill in two
config files, run bootstrap, have a working lab. Fire-and-forget provisioning: create correct
once, never police drift. This file is the map; contract detail lives in [specs/](specs/) and
`.claude/CLAUDE.md`.

## Modules

| Module | Responsibility | Spec |
|---|---|---|
| `ansible/playbooks/` | Orchestration entry points, one per UI job (bootstrap, apps/<app>, apps/remove, stacks/*, maintenance/*, proxmox/*, docker/*) | [one-click-idempotent](specs/one-click-idempotent.md) |
| `ansible/tasks/` | Shared task library: `load-user-vars`, `network/generate-ip`, `proxmox/{lxc,vm}-create` + `ip-to-vmid`, `stack/find-or-create-host`, `guest-bootstrap`, `bootstrap/*` | [namespace-merge-discipline](specs/namespace-merge-discipline.md) |
| `ansible/tasks/wiring/` + `unwiring/` | Register/deregister an app with each platform service (reverse proxy, SSO, uptime, DNS); one file per provider, conditional no-ops | [provider-noop-wiring](specs/provider-noop-wiring.md) |
| `ansible/roles/` | Per-app deployment (`_template-native`, `_template-docker` are the contracts; `docker` installs Docker Engine). Native roles ship `lab-update-check` / `lab-restart-app` / `lab-tail-applog` to `/usr/local/bin/` | [one-click-idempotent](specs/one-click-idempotent.md) |
| `ansible/vars/` | Config layers 1–2: `homelabinfra-defaults.yml` (global) and `app-defaults/<app>.yml` (per-app), both git-managed | [config-layering](specs/config-layering.md) |
| `config/` (gitignored) | Config layer 3: user's `proxmox.yml`, `infrastructure.yml`, `apps/<instance>.yml`, plus `.generated/facts.yml` written by bootstrap | [secrets-handling](specs/secrets-handling.md) |
| `ansible/inventory/proxmox.yml` | `community.proxmox` dynamic inventory → groups `proxmox_nodes`, `proxmox_clients`, `tag_<tag>`. Only `homelab-infra`-tagged guests are managed | [config-layering](specs/config-layering.md) |
| `semaphore/`, `rundeck/` | Importable UI job definitions; playbooks stay UI-agnostic | — |
| `.claude/meta/` | Pre-existing hand-written backlog of numbered slices (000–601) with its own INDEX.md; autobuild plans in `.claude/plans/` cross-reference it | — |

## Flows

### App deploy (the one-click path)

1. UI job runs `playbooks/apps/<app>.yml -e instance=<name>` on `localhost`.
2. **Play 1 — Provision**: `load-user-vars` merges defaults + user config; app defaults +
   `config/apps/<instance>.yml` merge into `app_config`. Docker apps call
   `stack/find-or-create-host` (locate `tag_<stack>` host or create one); native apps call
   `generate-ip` → `ip-to-vmid` → `lxc-create`. Target host lands in the `app_deploy` group via
   `add_host`, carrying `app_config` and `homelabinfra_infra` as hostvars.
3. **Play 2 — Deploy**: on the target guest — `guest-bootstrap` (once, guarded by the
   `homelab_bootstrapped` local fact), then the app role.
4. **Play 3 — Wire**: on localhost — reverse proxy route, Authentik provider, Uptime Kuma
   monitor, DNS record; each conditional on the configured provider in `homelabinfra_infra`.

```
Semaphore ──> [Play 1: localhost]──add_host──> [Play 2: guest] ──> [Play 3: localhost APIs]
                 load-user-vars                     guest-bootstrap      wiring/*.yml
                 find-or-create / lxc-create        role: <app>          (per provider)
```

### Bootstrap (run once)

`playbooks/bootstrap.yml` deploys baseline services in dependency order — Vaultwarden, Ntfy,
reverse proxy, Authentik, Uptime Kuma, Grafana stack, PBS — and after each one calls
`bootstrap/write-generated-facts.yml` to append that service's endpoint/token to
`config/.generated/facts.yml`, which every later deploy reads as `homelabinfra_infra`.

### Day-2 (configure tools, don't replicate them)

Watchtower updates containers, unattended-upgrades patches OS, PBS backs up — all configured at
deploy time, all reporting to Ntfy. Feedback loop: Watchtower "X updated" + Uptime Kuma "X is
DOWN" → user runs `stacks/rollback-container.yml`. Native apps: `maintenance/check-native-updates.yml`
(weekly) calls `lab-update-check` on managed hosts and notifies; re-running the app's deploy
playbook IS the update. `apps/remove.yml` mirrors deploy: stop app, run `unwiring/*` per provider;
`config/apps/<instance>.yml` survives as the restore point.

## Seams

- **Variable namespaces**: `homelabinfra_config` (merged input), `homelabinfra_instance`
  (computed execution facts), `homelabinfra_infra` (service registry from facts.yml). All writes
  go through `combine(recursive=True)` — see [namespace-merge-discipline](specs/namespace-merge-discipline.md).
- **Cross-play handoff**: facts are host-scoped; the only sanctioned way to move state between
  plays is `add_host` hostvars (Play 1 → Play 2) or `hostvars[...]` reads. This is the repo's
  most fragile seam — plays on `localhost` do not see facts set on proxmox nodes.
- **`config/.generated/facts.yml`** is the service registry: bootstrap writes it, wiring reads
  it. Wiring tasks take a fixed `wiring_*` variable contract from the calling playbook and never
  reach into app internals.
- **Provider abstraction**: `infrastructure.yml` declares provider *choices*; every wiring task
  is a per-provider file selected by name, and a missing provider is a no-op, not an error.
- **Proxmox boundary**: `community.proxmox` modules are API clients; `pct`/`qm` shell waits are
  the only node-local operations. All created guests carry the `homelab-infra` tag; untagged
  resources are never touched. **Execution model (decided 2026-07-02, applied by
  `decide-multinode-scoping`)**: provisioning plays run on `localhost` and call the Proxmox API;
  only `pct`/`qm` tasks are `delegate_to` the node named in `homelabinfra_config.proxmox.node`.
  Plays must never target `hosts: proxmox_nodes` with `run_once` facts — that pattern corrupts
  fact scoping on multi-node clusters.
