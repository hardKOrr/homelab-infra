# Architecture

Ansible-based homelab automation platform: one click in Rundeck or Semaphore deploys a fully
configured, cross-wired application on Proxmox. The repository is designed for others to clone:
fill in the user configuration, run bootstrap, and deploy a working lab. Provisioning is
fire-and-forget: create the correct state, but do not continuously police drift. This file is
the map; contract detail lives in [specs/](specs/) and [`AGENTS.md`](../AGENTS.md).

## Modules

| Module | Responsibility | Spec |
|---|---|---|
| `ansible/playbooks/` | Orchestration entry points, one per UI job (bootstrap, `apps/*`, `stacks/*`, `maintenance/*`, `proxmox/*`, and `docker/*`) | [one-click-idempotent](specs/one-click-idempotent.md) |
| `ansible/tasks/` | Shared task library for configuration loading, networking, Proxmox provisioning, stack-host selection, guest bootstrap, platform wiring, and bootstrap support | [namespace-merge-discipline](specs/namespace-merge-discipline.md) |
| `ansible/tasks/wiring/` + `ansible/tasks/unwiring/` | Register and deregister an app with reverse proxy, SSO, uptime, and DNS providers; missing providers are conditional no-ops | [provider-noop-wiring](specs/provider-noop-wiring.md) |
| `ansible/roles/` | Per-app deployment. `_template-native` and `_template-docker` define new-role structure; native roles ship the three `lab-*` maintenance scripts | [one-click-idempotent](specs/one-click-idempotent.md) |
| `ansible/vars/` | Git-managed global defaults, app defaults, media wiring data, and the authoritative variable contract | [config-layering](specs/config-layering.md) |
| `config/` (gitignored) | User configuration, app overrides, backups, and topology-only generated facts on the runner | [secrets-handling](specs/secrets-handling.md) |
| `ansible/inventory/proxmox.yml` | `community.proxmox` dynamic inventory. Only `homelab-infra`-tagged guests are managed | [config-layering](specs/config-layering.md) |
| `catalog/applications.yml` | Human-facing classification of deployable applications by purpose and type; projected into Rundeck as one folder per application, independent of hosting kind | — |
| `semaphore/`, `rundeck/` | Importable UI job definitions; playbooks stay UI-agnostic. `rundeck/render-job.py` projects the catalog into one folder per application and expands `rundeck/app-actions.yml` into that application's Maintenance jobs | [rundeck README](../rundeck/README.md) |
| `docs/` | Architecture and normative implementation specifications | — |
| `gate/` | Executable lint, parser, syntax, and focused regression checks | [gate README](../gate/README.md) |
| `docs/meta/` | Current work queue, numbered slices, lessons, and archived implementation history | — |

## Flows

### App deploy (the one-click path)

1. UI job runs `ansible/playbooks/apps/<app>.yml -e instance=<name>` on `localhost`.
2. **Play 1 — Provision**: `load-user-vars` merges defaults + user config; app defaults +
   `config/apps/<instance>.yml` merge into `app_config`. Docker apps call
   `stack/find-or-create-host` (locate the `_.stack+<stack>[-<estate>]` host or create one,
   so estates never share an ordinary stack host); native apps call
   `network/resolve-network` → `generate-ip` → `ip-to-vmid` → `lxc-create`. Target host lands
   in the `app_deploy` group via `add_host`, carrying `app_config` and `homelabinfra_infra`
   as hostvars. `resolve-network` turns the app's SCOPE into the network it is addressed on
   — `shared` for `scope: lab` and for a `shared: true` stack, the estate's network
   otherwise — and `ip-to-vmid` derives the guest's VMID from the address it was given.
3. **Play 2 — Deploy**: on the target guest — `guest-bootstrap` (once, guarded by the
   `homelab_bootstrapped` local fact), then the app role.
4. **Play 3 — Wire**: on localhost — reverse proxy route, Authentik provider, Uptime Kuma
   monitor, DNS record; each conditional on the configured provider in `homelabinfra_infra`.

```
UI job ──> [Play 1: localhost]──add_host──> [Play 2: guest] ──> [Play 3: localhost APIs]
            load-user-vars                     guest-bootstrap      wiring/*.yml
            find-or-create / lxc-create        role: <app>          (per provider)
```

### Bootstrap (run once)

`ansible/playbooks/bootstrap.yml` first reconciles Caddy and Vaultwarden, then deploys Ntfy,
Authentik, Uptime Kuma, the Prometheus and Grafana observability stack, and PBS. Each app records
its connection topology in `config/.generated/facts.yml` before a later dependency reads it.
Secrets are overlaid in memory from Vaultwarden and are never written to generated facts.

### Day-2 (configure tools, don't replicate them)

Watchtower updates containers, unattended-upgrades patches OS, PBS backs up — all configured at
deploy time, all reporting to Ntfy. Feedback loop: Watchtower "X updated" + Uptime Kuma "X is
DOWN" → user runs `ansible/playbooks/stacks/rollback-container.yml`. Native apps:
`ansible/playbooks/maintenance/check-native-updates.yml`
(weekly) calls `lab-update-check` on managed hosts and notifies; re-running the app's deploy
playbook IS the update. `ansible/playbooks/apps/remove.yml` mirrors deploy: stop app, run
`ansible/tasks/unwiring/*` per provider;
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
- **Kubernetes backend** (slice 204): `playbooks/apps/k3s-cluster.yml` builds a k3s cluster as an
  additional hosting backend beside native LXC and Docker stacks — it does not replace either, and
  no working app migrates onto it by default. `tasks/kubernetes/provision-node.yml` reuses
  `vm-clone.yml` and `ensure-cloud-template.yml` per node rather than adding a second way to make a
  VM; one template is built on each Proxmox node because local ZFS pools are not shared storage.
  Play 4 is `serial: 1` so exactly one node runs `--cluster-init` and the rest join it; the join
  token travels host to host in memory and is canonical only in Vaultwarden. The cluster publishes
  nothing itself — the API, node addresses and the MetalLB ingress VIP stay on private networks,
  and the platform Caddy remains the sole public TLS edge. Two properties are asserted rather than
  documented, because all are the kind that quietly stop being true: that every server sits on a
  distinct Proxmox node when `failure_domain_mode: distinct-nodes` is declared, that a
  quorum-only node still carries its `NoSchedule` taint, and that exactly one default
  StorageClass exists and is the platform's. That last one is asserted because it failed: k3s
  re-stages its packaged addon manifests on every service start, so demoting the bundled
  `local-path` class was undone by the next restart and left two defaults, with an unqualified
  PVC landing on whichever was older. `local-storage` is therefore disabled on the nodes and
  `roles/k3s_cluster` owns the provisioner Deployment, its RBAC and its ConfigMap outright —
  applied from the Configure play, never from the `serial: 1` founder play, whose node is the
  tainted quorum-only one. The default StorageClass is node-pinned, so a control plane that
  tolerates a node loss does **not** imply application data that does —
  `homelabinfra_infra.kubernetes.failure_domain_mode` and `storage_class` are what consumers read
  rather than counting nodes.
- **Proxmox boundary**: `community.proxmox` modules are API clients; `pct`/`qm` shell waits are
  the only node-local operations. All created guests carry the exact ownership tag `_+lab`;
  untagged resources are never touched. **Execution model (decided 2026-07-02, applied by
  `decide-multinode-scoping`)**: provisioning plays run on `localhost` and call the Proxmox API;
  only `pct`/`qm` tasks are `delegate_to` the node named in `homelabinfra_config.proxmox.node`.
  Plays must never target `hosts: proxmox_nodes` with `run_once` facts — that pattern corrupts
  fact scoping on multi-node clusters.
