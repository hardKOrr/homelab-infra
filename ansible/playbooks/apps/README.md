# Adding a New App

Six steps. Most of the work is step 4: the app's defaults and its role.

Deploy accepts `--tags provision` to resolve configuration and prepare the host,
storage and network access without deploying, recording or wiring the application.
This can prepare a destination GPU stack before an application's backup and move.
Run the full Deploy or Restore App afterwards to install the workload.

---

## Step 1 — Choose the hosting backend

Use the first condition that matches the application.

1. Keep a platform dependency outside Kubernetes when the cluster needs it to deploy,
   publish, unlock, recover, or back up the cluster. The current external boundary includes
   the runner, Caddy, Vaultwarden, Authentik, Proxmox, and PBS. This avoids a cluster
   failure taking down a service required to repair that cluster.
2. Use **Kubernetes** for a stateless OCI workload, or when scheduling, controlled rollout,
   or replica behavior provides a real benefit. A stateful workload fits when its data is
   easily restored or its StorageClass remains accessible after a node failure.
3. Use **Docker on LXC** for a single-replica stateful OCI application while its data is
   host-local, or for a Compose application that does not benefit from cluster scheduling.
   Related Docker applications may share an estate-resolved stack host.
4. Use **Native LXC** when the application ships as a package or single binary and direct
   OS or service integration is simpler than an OCI runtime.
5. Use **Docker on VM** only when an OCI workload needs a full kernel or device behavior
   that the LXC path cannot provide.
6. Use a **VM** when the application owns its installer, operating system, or kernel.

The current Kubernetes StorageClass is node-local. A volume pins its pod to one cluster
node, so the scheduler cannot fail that workload over while the node is unavailable.
Shared resilient storage can remove that limit, but it needs a supported provisioner and
volume migration, and it does not make a single-replica application highly available by
itself. Read [`../../tasks/kubernetes/README.md`](../../tasks/kubernetes/README.md) before
selecting Kubernetes for persistent data.

Proxmox OCI guests are not implemented as a hosting backend. OCI images currently run
through Docker or Kubernetes.

---

## Step 2 — Copy the templates and add the shim

An app is four files plus its catalog entry (Step 3). `playbooks/apps/deploy.yml` provisions,
deploys, wires and notifies for every app; the app supplies only data and its role.

```bash
# App defaults: hosting methods, resources, port, routing
cp ansible/vars/app-defaults/_template.yml ansible/vars/app-defaults/sonarr.yml

# Role: what runs on the guest or cluster
cp -r ansible/roles/_template-docker/ ansible/roles/sonarr/
# or, for the native methods:
cp -r ansible/roles/_template-native/ ansible/roles/sonarr/

# User-facing config example
cp config.example/apps/_template.example.yml config.example/apps/sonarr.example.yml
```

Replace `APP_NAME` with the application slug throughout the copied files. Then write the
entry point, `playbooks/apps/sonarr.yml`, as a shim on Deploy:

```yaml
---
# Deploy one sonarr instance: playbooks/apps/deploy.yml does the work, per-app behavior is in
# roles/sonarr and vars/app-defaults/sonarr.yml.
- name: Deploy sonarr
  ansible.builtin.import_playbook: deploy.yml
  vars:
    app: sonarr
```

Deploy provisions every native guest through `roles/_template-native/tasks/provision.yml`;
delete `tasks/provision.yml` from your copy. Kubernetes runs the role's `kubernetes.yml` instead of
`main.yml`; read [`../../tasks/kubernetes/README.md`](../../tasks/kubernetes/README.md) and
copy a role that already supports it. A role that serves several apps (the Servarr family)
is named in `role_aliases` in `gate/structure.yml` and in `deploy.role` in each app's defaults.

---

## Step 3 — Classify the application

Add the application to `catalog/applications.yml`:

```yaml
  sonarr:
    name: Sonarr                        # the folder name and the "Deploy Sonarr" job name
    summary: television library automation; run Wire Media Stack afterward.
    deploy_uuid: 0b6c...                # a new uuid4, never changed: Rundeck keys history on it
    root: Applications                  # Applications | Platform
    category: Media & Entertainment     # the broad purpose an operator recognizes
    type: Library Automation            # the application type; omit for a Platform entry
    scope: estate                       # REQUIRED. estate | lab — see below
```

`scope` says which side of the estate boundary the application sits on. Estates are
independent application, routing, identity, DNS, and network scopes. `estate` creates one
deployment per estate, with instances named `<app>-<estate>[-<variant>]`. `lab` creates one
deployment that serves every estate and is the deliberate exception; document why the
application crosses that boundary. There is no default. The renderer rejects an
application with no `scope`.

Application scope and hosting placement are related but not interchangeable. `scope: lab`
classifies one application deployment. `_.shared` marks hosting substrate that crosses
estate boundaries. Read [`../../tasks/proxmox/README.md`](../../tasks/proxmox/README.md) for
the tag grammar and [`../../vars/contract/`](../../vars/CONTRACT.md#5-required-vs-optional-keys-per-config-file) for estate and network
resolution.

Do not classify it by Docker, LXC, VM, Kubernetes, stack, database dependency, or another
execution detail. Add jobs that act on the lab rather than on one application to
`rundeck/job-groups.yml` instead.

The entry produces the whole folder: `render-job.py` projects the group
`<root>/<category>/<type>/<name>`, renders the Deploy job from `rundeck/jobs/deploy-app.yaml`
(the `summary:` is its one line of operator text: what the app is and anything to do before
or after a Deploy), and expands every applicable day-2 template into
`<that group>/Maintenance`. Which actions apply follows from facts in
`vars/app-defaults/<app>.yml` (`recovery.methods`, `hosting.supported`), so **Step 4 is what
decides the app's Maintenance folder**, and nothing here has to list its jobs.

Optional fields:

- `exclude: [rollback]` withholds a standard action the app cannot implement truthfully,
  with the reason in a comment beside it.
- `extra: [migrate]` opts into an app-specific action from `rundeck/app-actions.yml`
  `app_actions`. Only the Servarr family uses it today.
- `essential: true` withholds the Remove job, for a service the platform cannot run
  without.

`render-job.py --check` rejects an unclassified job, a source `group:` that has drifted from
its classification, a template no application selects, an `exclude:` that withholds nothing,
and any UUID collision.

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

**`roles/sonarr/tasks/*.yml` hooks**
- Add a Deploy, Remove or Restore hook (Step 5) only for a step Deploy cannot do from the
  defaults. Never add a shared step to the role that Deploy already runs.

**`config.example/apps/sonarr.example.yml`**
- Expose only the knobs a user might legitimately want to change
- Document each one with a comment explaining what changing it does
- Do NOT expose internal role variables — only app-facing config

---

## Step 5 — Add app-to-app integration when required

If the application needs API-level connections to peers, add that behavior to the relevant
stack integration playbook. Hosting on the same Docker stack is not, by itself, a reason to
couple applications.

```bash
# Edit the relevant stack wire playbook:
ansible/playbooks/stacks/wire-media-stack.yml
```

Make each integration idempotent by reading current state before creating or changing it.

Per-app removal state that `playbooks/apps/remove.yml` cannot know about, such as a
backup schedule, files outside `/opt/<instance>` or a dedicated device, goes in role
hooks under `roles/<app>/tasks/`, each run only when the file exists:

| Hook | Remove runs it |
| --- | --- |
| `remove-inputs.yml` | on localhost before unwiring: validate delete targets, declare `_rm_database_backend`, `_rm_keeps_guest_record` or `_rm_no_named_database` (this hosting method keeps no database on the named backend) |
| `remove-unwire.yml` | on localhost after provider unwiring, before the registry prune: withdraw the app from other apps |
| `remove-units.yml` | on a native guest: set `_rm_service_names` instead of `app.service_name` |
| `remove-native-data.yml` | on a native guest after the service stops, before the data paths go |
| `remove.yml` | after the Compose project stops |
| `remove-devices.yml` | after the data paths are gone; a GPU hook calls `tasks/proxmox/detach-app-gpu.yml` |

A converted app's Deploy is `playbooks/apps/deploy.yml`; its per-app steps are role hooks,
run only when the file exists:

| Hook | Deploy runs it |
| --- | --- |
| `deploy-prepare.yml` | on localhost after the hostname is known, before provisioning: may set `deploy_host_mounts` (attached by `tasks/proxmox/attach-host-mounts.yml`), `deploy_host_mounts_owner`, `deploy_host_mounts_own_node` (only the guest's node), `native_guest_database` and `firewall_inbound_ports` |
| `deploy-provision.yml` | on localhost after the database, before the shared stack or native guest step: provisions a guest of the app's own shape (`tasks/proxmox/provision-app-vm.yml`) into `deploy_<instance>` and sets `deploy_guest_provided` so the shared step skips |
| `deploy-record.yml` | on localhost before wiring, also while a restore target is isolated: registry entries other jobs read (`media`, `databases`); `deploy_notify_note`, a note computed from `app_config` |
| `deploy-wire.yml` | on localhost after wiring, never while a restore target is isolated: connections with deployed peers; a failure recorded through `report-degradation.yml` fails the run |
| `kubernetes.yml` | on localhost for `hosting.method: kubernetes`; it may run the whole `main.yml` |

Deploy reads these app-defaults keys (shared values in `vars/app-defaults/_base.yml`):
`routing.monitor_path`, `routing.auth_group`, `routing.auth_admin_only` (grant only the SSO
admin user), `deploy.kubernetes_upstream_port` (`app`: the proxy reaches a
Kubernetes app on the ingress address at `app.port`, not the ingress route on 80), `deploy.notify_note` (a sentence added to the notification), `vault.methods`
and `platform_layers` (`tasks/apps/load-config.yml`), `firewall.methods` (the methods whose
Deploy opens the declared `firewall:` access; default every method), `firewall.after_deploy`
(open the guest's access after the role ran, `tasks/apps/deploy-firewall.yml`), `firewall.sends_mail`
(SMTP egress to the platform relay). An `app.database` gets its database and login role on
the named backend before the guest is provisioned (`tasks/database/deploy-consumer.yml`),
on the methods in `deploy.database_methods` (default every method); with
`deploy.database_optional`, only while `app.database.instance` is named.

Never add an `app == '<app>'` branch to Remove. Restore App's hooks
(`restore-inputs.yml`, `restore-provisioned.yml`, `restore-config.yml`) are listed in the
header of `playbooks/maintenance/restore-app.yml`.

---

## Step 6 — Test

Record the new app's row in the app matrix (see [`gate/README.md`](../../../gate/README.md)):
`python gate/test-app-matrix.py --update > gate/app-matrix.yml`, and check that the row
lists the shared operations you meant the app to use.

```bash
bash gate/lint.sh
bash gate/test.sh
python rundeck/render-job.py --check rundeck/jobs
```

## Wiring Contract Reference

Deploy's record-and-wire play sets these variables before calling the wiring tasks, from the
app's defaults and the resolved estate. **Do not change the variable names** — all wiring
tasks depend on them.

| Variable | Value | Used by |
|---|---|---|
| `wiring_app_name` | `{{ instance }}` | All wiring tasks — used as slug/ID |
| `wiring_upstream_host` | Address reached by the reverse proxy | Caddy, Nginx |
| `wiring_upstream_port` | App listen port | Caddy, Nginx |
| `wiring_subdomain` | `routing.subdomain`, default `{{ instance }}` | builds `wiring_domain` / monitor URL |
| `wiring_domain` | `subdomain.estate-domain` | Caddy, Nginx, Authentik, DNS |
| `wiring_app_display` | Human label | Authentik, Uptime Kuma |
| `wiring_monitor_url` | Public HTTPS URL | Uptime Kuma |
| `wiring_auth_group` | Authentik group name | Authentik |
| `wiring_identity_mode` | `routing.identity`, default `catalog` | Authentik mode dispatch and reverse-proxy `forward_auth` enforcement |
| `wiring_access` | `routing.access`, default `internal` | reverse-proxy access policy |

Deploy also runs `tasks/resolve-estate.yml` before wiring: it overlays the app's
`routing.estate` domain/sso/dns facts onto `homelabinfra_infra`, so wiring tasks
stay estate-agnostic.
