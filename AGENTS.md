# homelab-infra

Ansible-based homelab automation platform. Goal: one click in Rundeck or Semaphore deploys a fully configured, cross-wired application on Proxmox. Designed to be shared — others clone, supply their lab configuration, run bootstrap, and have a working lab.

## Where the Rules Live

This file is the concise project instruction source: facts, layout, conventions, and the
commands that verify a change. It is deliberately short. When it and a deeper document
disagree, the deeper document wins on its own subject:

| Subject | Authority |
|---|---|
| `homelabinfra_*` variable shapes and config file schema | `ansible/vars/CONTRACT.md` |
| Reviewable code contracts (layering, merges, Jinja, secrets, wiring, one-click) | `docs/specs/` |
| Module, flow, and seam map | `docs/architecture.md` |
| Gate scope, timing, and WSL bootstrap | `gate/README.md` |
| Work state, past decisions, and standing lessons | `docs/meta/` — history, never normative |

Do not add rationale to this file. Decisions and failed attempts belong in `docs/meta/` and
in Git history.

## Philosophy

- **Fire-and-forget provisioning** — create correct once, not drift enforcement. We do not police drift.
- **Defaults cover 80% of homelabs** — users only configure what differs.
- **homelab-infra manages what it creates** — no "bring your own host" support. Existing untagged resources are ignored entirely.
- **We configure tools, we do not replicate them** — Watchtower handles container updates, unattended-upgrades handles OS updates, PBS handles backups. We configure these at deploy time, not build our own.
- **No Ansible Vault** — Vaultwarden is mandatory after cutover. Only its dedicated automation unlock credentials and server-admin token remain in encrypted runner control-plane storage.
- **One click per app in Rundeck or Semaphore** — not "deploy media stack with checkboxes". Each app is a separate job.

## Repository Structure

```
ansible/
  playbooks/
    bootstrap.yml                  # one-time platform setup
    apps/
      <app>.yml                    # deploy app (idempotent — re-run = update config/binary)
      remove.yml                   # remove app: stop container, unwire Caddy/Authentik/Uptime Kuma/DNS
      migrate-servarr.yml          # stage an existing Servarr app's config onto a lab instance
      k3s-cluster.yml              # build/converge the k3s cluster (the Kubernetes hosting backend)
    proxmox/
      create-lxc.yml
      create-vm.yml
    docker/
      create-docker-host.yml
    stacks/
      wire-media-stack.yml         # wire all app-to-app connections for the media stack (idempotent)
      rollback-container.yml       # pin container to previous image tag, restart, notify via Ntfy
    maintenance/
      status.yml                   # read-only: what's running, what's down, what's behind on updates
      check-native-updates.yml     # compare installed vs latest GitHub release for native LXC apps, notify via Ntfy
      restart-app.yml              # restart a native app via lab-restart-app; param: instance
      tail-applog.yml              # tail app logs via lab-tail-applog; params: instance, lines
      config-doctor.yml            # validate the config/ tree without deploying anything
      get-config.yml               # print one instance's merged effective config
      configure-app.yml            # edit one config/apps/<instance>.yml key through the audited write path
      store-secret.yml             # put one secret into its canonical Vaultwarden item
      vaultwarden-enroll.yml       # enrol the human owner and the automation account
      vaultwarden-cutover.yml      # verify the vault, then write the vault-mode state file
      vaultwarden-recovery.yml     # re-establish access after a lost automation credential
  tasks/
    load-user-vars.yml
    resolve-estate.yml             # overlay the app's routing.estate scope onto the infra facts
    notify.yml                     # the one notification seam; no-op when the provider is none
    report-degradation.yml         # record a failure without aborting the run
    assert-no-degradations.yml     # last task of a play: fail if anything recorded was fatal
    network/generate-ip.yml
    config/
      run-doctor.yml               # run scripts/config-doctor.sh and fail the play on an error
      write-config-file.yml        # the only path that writes into config/; backs up and diffs
    bitwarden/                     # Vaultwarden session, canonical item read, item upsert, teardown
    vaultwarden/token-sink.yml     # where the controller keeps the Vaultwarden admin token
    kuma/                          # Uptime Kuma socket.io transport (it has no REST API)
    proxmox/
      lxc-create.yml
      vm-create.yml
      vm-clone.yml                 # shared clone seam; also used by kubernetes/provision-node.yml
      ensure-cloud-template.yml
      ensure-guest-running.yml
      ip-to-vmid.yml
      register-nodes.yml
      resolve-startup.yml          # boot order and delay for a guest
      record-app-on-guest.yml      # stamp tag app_<instance> + a notes row on the guest
      attach-host-mounts.yml       # attach storage the node already mounts to a guest
    kubernetes/                    # Kubernetes hosting backend seams
      provision-node.yml           # one cluster VM, via the shared vm-clone seam
      resolve-cluster.yml          # read cluster topology + delegate from generated facts
      ensure-namespace.yml         # one instance owns exactly one namespace app-<instance>
      sync-secret.yml              # namespace-scoped runtime copy of a canonical Vaultwarden item
      apply-manifest.yml           # apply a role-rendered manifest into that namespace
    wiring/                        # platform wiring tasks (conditional on provider)
      caddy.yml
      nginx.yml
      authentik.yml
      uptime-kuma.yml
      opnsense.yml
      pihole.yml
    app-wiring/                    # app-to-app wiring, driven by vars/media-wiring.yml
      resolve-media-registry.yml
      prowlarr-application.yml     # one *arr → a Prowlarr Application (indexer sync)
      arr-download-client.yml      # one download client → one *arr
      bazarr-arr.yml               # Bazarr → one Sonarr / one Radarr
    unwiring/                      # inverse of wiring/ — called by remove.yml
      caddy.yml
      nginx.yml
      authentik.yml
      uptime-kuma.yml
      opnsense.yml
      pihole.yml
      kubernetes.yml               # delete the instance's namespace
      registry.yml                 # forget the instance in config/.generated/facts.yml
      guest-record.yml             # withdraw the guest tag and notes row
    guest-bootstrap.yml            # post-provisioning: packages, hostname, timezone, unattended-upgrades + Ntfy hook
    stack/
      find-or-create-host.yml      # find existing tag_<stack> host or provision new one; adds to app_deploy group
    bootstrap/
      write-generated-facts.yml   # writes config/.generated/facts.yml after each baseline service
      configure-pbs.yml
      configure-watchtower.yml
      configure-unattended-upgrades.yml
  roles/
    docker/                        # installs Docker Engine (Debian only)
    _template-native/              # copy for new native LXC apps; includes files/ with lab script placeholders
    _template-docker/              # copy for new Docker apps
    k3s_cluster/                   # builds the cluster itself — not an app role
    servarr/                       # Sonarr/Radarr/Lidarr/Prowlarr — one role, four apps
                                   # (same program, different media type; see the role header)
    <app>/                         # one role per deployable app; ships files/lab-* scripts
  vars/
    CONTRACT.md                    # authoritative variable-loading contract for homelabinfra_*
    homelabinfra-defaults.yml      # global defaults (git-managed)
    stack-defaults.yml             # sizing per stack host, so it does not depend on deploy order
    media-wiring.yml               # media app kinds: API versions, implementations, categories
    app-defaults/<app>.yml         # per-app sensible defaults (git-managed)
  scripts/                         # committed helpers the playbooks and the runner shell out to
    lab-run.sh                     # runner entrypoint: refresh the checkout, then run one job
    config-doctor.sh               # validate the config/ tree
    redact-config.sh               # strip secrets from a config dump
    vault-runtime.py               # build the in-memory Vaultwarden runtime contract
    allocate-ip.py, registry-forget.py, secret-shape.py, semaphore-run.sh,
    resolve-python.sh, with-proxmox-env.sh
  files/
    proxmox/guest-app-record.py    # read-modify-write of a guest's tags and notes region
  inventory/
    proxmox.yml

semaphore/
  project.json                     # importable Semaphore project with all job templates

rundeck/
  jobs/
    *.yaml                         # importable Rundeck job definitions

config/                            # GITIGNORED — never overwritten by git pull
  proxmox.yml                      # Proxmox connection shape; secrets come from Vaultwarden
  infrastructure.yml               # platform service role declarations
  .generated/
    facts.yml                      # written by bootstrap: topology only, never secrets
  apps/
    <instance>.yml                 # one per app instance (persists after removal = restore point)

config.example/                    # in git — fully documented templates for users to copy
  proxmox.yml
  infrastructure.yml
  apps/<app>.example.yml

docs/
  README.md                        # entry point: what is normative here and what is history
  architecture.md                  # module, flow, and seam map
  specs/                           # normative implementation and review contracts
  meta/                            # work state and history — never normative
    INDEX.md                       # the work queue, kept as a table
    LESSONS.md                     # standing facts that outlived the slice that produced them
    <NNN>-<slug>/                  # one live slice; done/ and no-target/ hold the rest

gate/                              # lint, parser, syntax, link, and focused regression checks
  README.md                        # exact scope rules and the one-time WSL bootstrap
```

## Config Hierarchy

Three layers merged via `combine(recursive=True)` at playbook runtime:

1. `ansible/vars/homelabinfra-defaults.yml` — global defaults
2. `ansible/vars/app-defaults/<app>.yml` — per-app defaults (cores, RAM, ports, stack assignment)
3. `config/apps/<instance>.yml` — user overrides for this instance only

Users only write what differs. Everything else falls through.

## Variable Namespaces

- `homelabinfra_config.*` — merged user + default config (input layer)
- `homelabinfra_instance.*` — computed execution-time facts (built by task files)
- `homelabinfra_infra.*` — topology from generated facts overlaid with the in-memory Vaultwarden runtime contract

**CRITICAL**: Always use `combine(recursive=True)` when setting keys on any of these dicts.
Never `set_fact: homelabinfra_instance: {key: val}` — it destroys all sibling keys.

## Hosting Types

| Type | Use when | Examples |
|---|---|---|
| Native LXC | Single-binary or package-installed services | Pihole, Caddy, Vaultwarden |
| Docker on LXC | Multi-container stacks | Authentik, media stack, monitoring |
| Docker on VM | Needs full kernel | kernel module deps |
| VM | Needs own installer or full OS | PBS, the k3s cluster nodes |
| Kubernetes | Ordinary OCI workload that wants scheduling and restart behavior | Mixpost (pilot) |

Proxmox OCI guests are outside this project's supported hosting model. That is a
statement about the Proxmox guest type, not about OCI images: an OCI image runs here
either under Docker on a stack host or as a Kubernetes workload.

### Which one to start with

Start with **Docker on LXC** for anything that ships a Docker image. It is the safe
default, it is what most of this repository already does, and a working Docker app has no
reason to move.

Choose the others only for the reason in their row:

- **Native LXC** when the app is a single binary or an apt package and Docker would only
  add a layer. Baseline platform services take this path.
- **VM** when the app owns its installer or needs its own kernel — PBS, and the k3s nodes
  themselves.
- **Kubernetes** as a deliberate choice for a new app, never as a migration of a working
  one. `hosting: kubernetes` is the only hosting kind that is declared rather than
  inferred; native and Docker are still told apart by the presence of `stack:`.

**What the Kubernetes backend does and does not buy.** It buys scheduling, restart and a
rolling update that a compose file does not have. It does not buy application-level high
availability: the default StorageClass is node-pinned, so a pod whose volume lives on an
unavailable node stays `Pending` rather than moving. The control plane tolerates one node
loss; the application data does not. Prefer the backend for stateless or easily-restored
workloads, and read
`homelabinfra_infra.kubernetes.failure_domain_mode` / `storage_class` rather than counting
nodes when describing availability.

Caddy, Vaultwarden, both Authentik estates, the runner and Proxmox/PBS stay **outside** the
cluster by decision: the cluster's own publishing, secrets and backup paths depend on them.
The platform Caddy remains the sole public TLS edge, and it proxies to one stable internal
ingress VIP — the cluster publishes nothing itself.

`ansible/playbooks/apps/README.md` step 1 carries the same table for the app-author path,
and the standing decisions and their rationale are in
`docs/meta/204-kubernetes-hosting-backend/`.

## Stack Model

Related Docker apps group onto shared hosts ("stacks"). Stack assignment is declared in `ansible/vars/app-defaults/<app>.yml` and overridable per-instance in `config/apps/<instance>.yml`. Stack host is created on first app deploy targeting it, then reused for subsequent apps on that stack. Proxmox tags identify stacks: `tag_media_stack`, `tag_services_stack`, etc.

## Wiring Step

Every app deployment ends by registering with platform services. Each task is conditional on the configured provider — missing providers are no-ops, not errors:

1. Caddy or Nginx route (if `infrastructure.reverse_proxy.provider != none`)
2. Authentik registration per identity mode (`routing.identity`, if `infrastructure.sso.provider: authentik`): `catalog` = launch tile only (default), `oidc` = OAuth2 provider + Application (client creds handed back to the deploy), `forward_auth` = proxy provider enforced at the reverse proxy, `none` = skipped
3. Uptime Kuma monitor (if Uptime Kuma instance is reachable)
4. DNS record (if `infrastructure.dns.provider != none`)

Wiring tasks read service connection details from `config/.generated/facts.yml`.
Before wiring, `ansible/tasks/resolve-estate.yml` overlays the app's `routing.estate`
scope (domain, sso, dns) onto those facts — multi-domain labs declare estates in
`infrastructure.yml` under `domains:`; single-domain labs are untouched.

**A configured provider that fails is not a no-op.** A provider set to `none` is skipped
silently; a provider that is declared and then does not do what it was asked must record the
failure with `ansible/tasks/report-degradation.yml` and let the play continue, and the play's
last task must be `ansible/tasks/assert-no-degradations.yml`. The run therefore attempts
everything it can and then refuses to exit 0, so the operator gets one message naming every
problem. Never catch a wiring failure, print it with `debug`, and finish green.

## Baseline Apps (Bootstrap Order)

`ansible/playbooks/bootstrap.yml` deploys in dependency order. Each app records topology before
the next app needs it:

1. **Caddy** — current reverse-proxy implementation; Layer 1 may have already created it
2. **Vaultwarden** — enforced, all platform secrets live here after cutover
3. **Ntfy** — notification hub (Watchtower, unattended-upgrades, Uptime Kuma all report here)
4. **Authentik** — SSO, optional per `infrastructure.yml`
5. **Uptime Kuma** — uptime monitoring, auto-registers all subsequent app deploys
6. **Prometheus + Grafana** — metrics and dashboards
7. **PBS** — backup, schedule configured by Ansible, runs autonomously

Nginx wiring exists, but an Nginx deployment playbook is not implemented. Do not describe Nginx
as a selectable bootstrap target until that playbook exists.

## Day-2 Operations

All operations are idempotent and re-runnable. State-changing jobs call the common notification
task. Ntfy publishing is implemented; `none` is a no-op. Gotify and Discord are declared provider
values but do not yet have publishers.

| Concern | Tool | Our responsibility | Notification |
|---|---|---|---|
| Container updates | Watchtower | Configure at Docker host creation | Ntfy: "X updated to vY — run Rollback if broken" |
| Container rollback | `rollback-container.yml` | Semaphore/Rundeck job, takes container name + image tag | Ntfy: "X rolled back to vY" |
| OS updates | unattended-upgrades | Configure in `guest-bootstrap.yml` with systemd drop-in → Ntfy | Ntfy: "N packages updated on hostname" |
| Native LXC app updates | `check-native-updates.yml` (scheduled weekly) | Calls `lab-update-check` on all managed hosts, aggregates JSON results | Ntfy: "Vaultwarden vX.Z available, you have vX.Y — re-run deploy to update" |
| App restart | `restart-app.yml` | Calls `lab-restart-app` on named host; param: instance | Ntfy: "X restarted" |
| App log tail | `tail-applog.yml` | Calls `lab-tail-applog` on named host; output to job console | Job console |
| Backups | PBS | Configure schedule + datastore in bootstrap | PBS native notifications |
| Uptime alerts | Uptime Kuma | Auto-register each app at deploy time | Ntfy: "X is DOWN / recovered" |
| App removal | `remove.yml` | Semaphore/Rundeck job — stops container, unwires everything | Ntfy: "X removed" |
| Lab status | `status.yml` | Semaphore/Rundeck job — read-only | Console/Semaphore output |
| App-to-app wiring | `wire-media-stack.yml` | Semaphore/Rundeck job — idempotent, safe to re-run | Ntfy: "Media stack wired: N connections confirmed" |
| Config validation | `config-doctor.yml` | Semaphore/Rundeck job — read-only, deploys nothing | Console/Semaphore output |
| Config edit | `configure-app.yml` | Writes one key through `tasks/config/write-config-file.yml` — backs up to `config/.backups/` and diffs into the job log | Job console |
| Secret storage | `store-secret.yml` | Puts one secret into its canonical Vaultwarden item | Job console (never prints the value) |

### Feedback Loop (Container Updates)
Watchtower fires "X updated" → Uptime Kuma fires "X is DOWN" → user correlates timestamps → runs Rollback Container job.
Watchtower notification includes the rollback instruction so the path is obvious without digging through docs.

### Native LXC App Update Path
Re-running the deploy playbook for a native app IS the update mechanism — it checks latest version, downloads if newer, restarts if changed. `check-native-updates.yml` (run on schedule) only notifies; it does not update.

### Lab Maintenance Scripts
Each native app role ships three scripts to `/usr/local/bin/` (installed by the role, placeholders in `ansible/roles/_template-native/files/`):
- `lab-update-check` — outputs JSON `{"app":..., "installed":..., "latest":..., "update_available":...}`. Each app owns its own version-check logic.
- `lab-restart-app` — restarts the app's service. Called by `restart-app.yml`.
- `lab-tail-applog` — streams recent logs (journalctl or equivalent). Called by `tail-applog.yml`.

All three are no-ops (exit 1) in the template — each app role replaces them with real implementations in `ansible/roles/<app>/files/`.

## UI Job Structure (Semaphore + Rundeck)

Both are supported. Playbooks are UI-agnostic. Job definitions live in `semaphore/` and `rundeck/` and are importable.

```
Bootstrap
  Bootstrap Platform          ← bootstrap.yml (run once)

Per-App
  Deploy <App>                ← apps/<app>.yml  (one job per app; instance baked in, no params)
  Remove App                  ← apps/remove.yml (params: instance, app (optional), delete_data)

Backend
  Deploy k3s Cluster          ← apps/k3s-cluster.yml (idempotent; re-run converges the cluster)

Per-Stack
  Wire Media Stack            ← stacks/wire-media-stack.yml (no params)
  Rollback Container          ← stacks/rollback-container.yml (params: stack, container, image_tag)

Maintenance
  Lab Status                  ← maintenance/status.yml
  Check Native App Updates    ← maintenance/check-native-updates.yml (scheduled weekly)
  Restart App                 ← maintenance/restart-app.yml (param: instance)
  Tail App Log                ← maintenance/tail-applog.yml (params: instance, lines)

Config and Secrets
  Config Doctor               ← maintenance/config-doctor.yml
  Get Config                  ← maintenance/get-config.yml (param: instance)
  Configure App               ← maintenance/configure-app.yml (params: instance, key, value)
  Store Secret                ← maintenance/store-secret.yml
  Vaultwarden Enrollment      ← maintenance/vaultwarden-enroll.yml
  Vaultwarden Cutover         ← maintenance/vaultwarden-cutover.yml
  Vaultwarden Recovery        ← maintenance/vaultwarden-recovery.yml
```

`rundeck/jobs/` is the complete set and also carries `reimport-jobs.yaml`, which reloads the
job definitions from the checkout. `semaphore/project.json` currently lags it — it has no
template for `Deploy k3s Cluster`, `Deploy Mixpost`, or the three Vaultwarden jobs. Adding a
playbook means adding a job on both sides.

## Secrets

Vault mode has narrow external control-plane material: the dedicated automation account's
`BW_CLIENTID`, `BW_CLIENTSECRET`, and `BW_PASSWORD`, plus the Vaultwarden server-admin
token. Rundeck injects these from project-scoped AES-GCM Key Storage; Semaphore uses
encrypted secret variables. The admin token cannot decrypt vault items.

Fresh bootstrap is Caddy → Vaultwarden → human owner/automation enrollment → verified
cutover → Bootstrap Platform. Before cutover, minimum seed files supply Proxmox/SSH/admin
material. After `/etc/homelab-infra/state/vault-mode` is written, ordinary jobs ignore
those files and fail before Ansible when Vaultwarden cannot unlock. No secret is printed.

Everything else is generated into canonical organization-owned Vaultwarden items and
verified before use. `config/.generated/facts.yml` is topology-only. Slice 014 closed on
2026-08-17 after live cutover, fail-closed, recovery, and fresh scratch-runner acceptance.

## Infrastructure Config (`config/infrastructure.yml`)

Declares roles, provider choices, and non-secret connection shape. Generated topology goes
in `.generated/facts.yml`; credentials go in canonical Vaultwarden items.

```yaml
# Optional multi-domain: a `domains:` map of named estates (own domain + own
# Authentik per estate, optional per-estate dns_challenge token for Caddy).
# The plain `domain:` scalar remains the single-estate shorthand.

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

## What a Guest Records About Itself

Every app deploy stamps the guest it lands on, and every removal withdraws that stamp
(`ansible/tasks/proxmox/record-app-on-guest.yml` and `ansible/tasks/unwiring/guest-record.yml`,
both driving `ansible/files/proxmox/guest-app-record.py`):

- the tag `app_<instance>` alongside the guest's existing tags, which yields a
  `tag_app_<instance>` inventory group and a filter in the Proxmox guest tree
- a row in a marker-delimited region of the guest's notes, listing instance, hosting kind,
  published URL and the date the record was written

A shared stack host therefore has many writers on one description field, so both writes are
read-modify-write keyed by instance: rows and tags belonging to other apps come back
byte-identical, and a re-deploy that changes nothing writes nothing. Everything above the
markers is operator-owned text and is never rewritten. The record is bookkeeping — a guest
that has vanished or a Proxmox that refuses the update never fails a deploy.

## Verifying a Change

Two gates are invoked from the repo root:

```
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

No absolute path is baked into either command. `wsl` inherits the Windows working directory,
so both resolve against whatever checkout you are standing in — a hard-coded home directory
would only be correct on the machine it was written on.

The wrappers narrow to affected files when the working tree permits it. A clean tree, `--all`,
or a change to a shared Ansible or gate path runs the full repository. **Allow at least 20 minutes
per full gate.** Full-repository lint and syntax checks are slow on
the `/mnt/c` filesystem and commonly run far longer than one minute. Do not wrap either
command in a short timeout. If the caller times out, its WSL child can keep running and hold
the `ansible-lint` lock; inspect the existing process and wait for or stop that exact process
before retrying. Do not start a second lint gate while the first is still running.

`lint.sh` runs ansible-lint (profile `min`), the Jinja parser check, and `check-links.py`, which
validates repository-local Markdown links. `test.sh` runs `--syntax-check` over its
selected playbooks and focused regressions for Vaultwarden handling, IP allocation, and registry
removal. Neither contacts Proxmox — both override `ANSIBLE_INVENTORY` to `localhost,` so the
dynamic inventory plugin never asks for credentials. See `gate/README.md` for exact scope rules.

**Run both under WSL.** `test.sh` invokes the interpreter at
`$HOME/.venvs/homelab-ansible/bin/ansible-playbook`. Under WSL that resolves; from Git Bash
on Windows `$HOME` is the Windows profile directory, the path does not exist, and every
playbook reports a syntax failure that has nothing to do with your change. A wall of
identical failures across playbooks you never touched means you ran the gate from the wrong
shell, not that you broke something.

Both scripts export `ANSIBLE_CONFIG` to an absolute path deliberately. The repo lives on
`/mnt/c`, which Ansible treats as world-writable and therefore refuses to load a
cwd-relative `ansible.cfg` from — without the override, `roles_path` never loads and
role-using playbooks falsely fail with "role not found".

## Gitignored Paths That Are Load-Bearing

`/config/` and `/artifacts/` are ignored, anchored to the repo root, and hold real user
configuration and secrets on the runner. `lab-run.sh` refreshes the runner's checkout with
`git reset --hard origin/<branch>` before every job; that reset is safe only because
nothing under `config/` is tracked.

The leading slash matters. A bare `config/` matches any directory of that name at any depth,
and it silently excluded `ansible/tasks/config/` — shipped code that existed in the working
tree but never reached GitHub, so both gates passed green and the runner died on a missing
file. Only the repo-root `config/` is user config; anything named `config/` deeper in the
tree is ours and belongs in git. See the comment in `.gitignore` for the full account.

Never commit anything under `/config/`. Users copy `config.example/` into place themselves.
