# Proxmox automation boundary

This directory contains shared Proxmox provisioning and guest-record tasks.

## Execution model

Provisioning plays run on `localhost` and use Proxmox API modules. A task that requires
`pct`, `qm`, or another node-local command delegates only that task to the selected
Proxmox node. Do not target all `proxmox_nodes` and use `run_once` to choose a node; facts
set by that pattern belong to the selected inventory host rather than to `localhost`.

The shared creation seams are `lxc-create.yml`, `vm-create.yml`, and `vm-clone.yml`.
Kubernetes node provisioning uses `vm-clone.yml`; it does not maintain a second VM
creation path.

`resolve-guest-placement.yml` is the placement source for those creation seams. The
`database_vm` class pins PostgreSQL/Redis to `node_roles.storage` and
`storage_ids.database_vm`; `floating_vm` uses the first online node from the ordered
`node_roles.compute` list and `storage_ids.floating_vm`; `floating_lxc` uses that same
compute order and `storage_ids.floating_lxc`. `gpu_vm` uses the iSCSI VM store and the
common migratable CPU model, but pins the guest to its dedicated device's declared compute
node. Its `proxmox.devices` declaration must name that node and an existing PCI resource
mapping. The database template is built on the pinned storage node and storage so its full
clones inherit the declared local zvol block size. The global
`proxmox.node` remains the control node for cluster-wide queries and non-classed paths.

`attach-host-mounts.yml` routes stack directories by Proxmox guest type. LXC guests keep
their existing mountpoint/idmap path. Stack VMs use one cluster directory mapping per
host path and eligible node set and a `virtiofs` device mounted at each requested guest path. Before
creating a mapping it checks the path on every node that can run the VM; an existing
mapping is read-only and must already match those nodes and paths. It persists the guest
mount in `/etc/fstab`. A VM with virtiofs cannot live-migrate; moving it requires an
offline migration or an HA restart.

The pinned VM module omits NIC changes on safe updates. `vm-clone.yml` enables NIC
configuration only after that invocation successfully creates a clone, before its first
boot, so the declared guest network reaches the VM alongside cloud-init addressing.
Existing guests and clone no-ops retain their NIC/MAC; this update supplies no disk
attachments. Before configuration or start, a reused non-running VM must already have
the declared NIC model, bridge and VLAN tag. A mismatch fails without mutation, including
a clone left unconfigured by an interrupted earlier run. The comparison preserves MACs
and other NIC options; stopped state alone does not establish that a VM has never booted.
SSH timeout remains fatal and names the guest and expected NIC/cloud-init
configuration. A mismatched existing clone
requires a supported recovery or recreation path.

PBS's separate existing-tag branch calls `validate-vm-network.yml` before adding its
guest to the deploy group or starting it. It reads current and pending NIC fields for
running and stopped guests, verifies current ownership, and compares model/bridge/VLAN
against the scope-resolved effective declaration. It performs no guest writes. Matching
reuse retains NIC/MAC, disks and data. The [PBS guide](../../roles/pbs/README.md) owns its
independent recovery prerequisites and supported-path limits; a NIC refusal is not
authority to recreate the guest.

## LXC creation and existing guests

`lxc-create.yml` is a creation seam. Application and stack callers select existing
owned guests through the inventory reuse path before calling it. It requires complete
inventory and refuses a VMID or hostname collision, including an owned existing guest;
use the supported reuse path instead of re-entering creation. Its module arguments fix
`state: present` and `update: false` after the allowlist. If a resource appears after
inventory selection, the module returns without mutation and the seam refuses all
subsequent node-local configuration.

The direct [`create-lxc.yml`](../../playbooks/proxmox/create-lxc.yml) entry point and
the LXC path of [`create-docker-host.yml`](../../playbooks/docker/create-docker-host.yml)
have no inventory reuse branch and are create-only. Re-running either against an
existing pinned address/VMID refuses creation instead of updating that guest in
place. Use the application or stack deployment path for existing-guest reuse.

Pinned `community.proxmox` 2.0.0 defaults to `update: true` and `cmode: default`.
Creation strips that sentinel, while existing updates can forward it to PVE. Disabling
updates at this creation boundary preserves existing console, network and storage
settings without choosing a replacement enum. `cmode` remains outside the repository
allowlist.

## Asynchronous task completion

`wait-for-task.yml` is the common completion boundary for asynchronous PVE and PBS
operations. Callers pass a delegated Proxmox node and the returned `UPID`; the helper
polls the task until it is stopped, rejects timeout, cancellation, or failure, and never
interprets task submission as successful backup or restore completion.

## Ownership and tag grammar

homelab-infra manages only resources carrying the exact `_+lab` ownership tag. A leading
underscore alone identifies the platform tag namespace; it does not grant ownership.

| Tag | Meaning | Inventory group |
| --- | --- | --- |
| `_+lab` | Resource created and managed by homelab-infra | `lab_managed` |
| `_-<fact>` | Durable machine fact such as `debian`, `docker`, or `k3s` | `lab_fact_<fact>` |
| `_.stack+<name>` | Docker stack host | `lab_stack_<name>` |
| `_.cluster+<name>` | Cluster member | `lab_cluster_<name>` |
| `_.template` | Managed cloud template | `lab_template` |
| `_.shared` | Hosting substrate deliberately shared across estates | `lab_shared` |
| `_.dev+<slug>` | Device-passthrough binding this platform created (see *Device passthrough* below) | none — scoped to the guest's own tags, not translated into an inventory group |
| `_<instance>` | Logical hosting unit for an application instance | `lab_app_<instance>` |

`ansible/inventory/proxmox.yml` owns the translation from tags to group names.
`tag-group.yml` translates one tag for dynamic lookups. Keep those expressions equivalent;
do not open-code another translation.

Proxmox templates are filtered out before inventory groups are built, including managed
templates. Template tasks locate them through the Proxmox node instead of dynamic guest
inventory.

## Inventory availability and trust

The supported inventory source uses the local `homelab_proxmox` plugin, extending the
pinned `community.proxmox` parser. It binds `validate_certs` explicitly on each inventory
request. Requests otherwise lets `REQUESTS_CA_BUNDLE` or `CURL_CA_BUNDLE` override a
session's `verify=false` when the request omits `verify`. Explicit `false` preserves the
independent Proxmox policy; `true` still consumes declared CA environment trust. This does
not change the wrapper's shared CA exports or other providers' verification settings.

Inventory parse failures are fatal, including when another inventory source parses and
when a refresh fails after a successful initial read. The completion marker is published
only after the entire parse succeeds and every node's guests can be enumerated. Offline or unknown
nodes remain in a successfully parsed diagnostic inventory, with the completion marker
false and `homelabinfra_proxmox_inventory_offline_nodes` identifying the missing coverage.
Status reports that incomplete view; ascent verification reports it and fails its final
health check. Malformed responses remain fatal. Neither case proves guest absence.
A healthy inventory with no guests does establish absence and may allocate.
`assert-inventory.yml` guards address allocation and the shared tag lookup even when an
unsupported static inventory would otherwise supply an empty group. Tag lookup requires
each matching guest's exact ownership tag, node, VMID and type; instance and stack tags
must select at most one guest. Cluster tags may select multiple owned guests.

## Device passthrough

`attach-shared-device.yml` binds a host device node into one or more LXC guests; several
guests may hold it at once. `attach-usb-passthrough-lxc.yml` resolves an operator-owned USB
resource mapping to the current `/dev/bus/usb` node and then uses the shared LXC seam with
a conflict check. `attach-pci-passthrough.yml` and `attach-usb-passthrough.yml` assign a
PCI resource mapping or USB resource mapping to exactly one VM guest, exclusively. All
seams assert `_+lab` ownership before writing, and platform-created assignments carry a
`_.dev+<slug>` provenance tag. GPU-capable catalog apps use dedicated GPU VMs; shared
`/dev/dri` binds are only for LXC callers.

Before inspecting the node or a guest, an attachment request must match exactly one named
entry in `homelabinfra_config.proxmox.devices`, whose explicit `mode` must agree with the
seam. A declaration's optional `node` scopes it to one `proxmox.nodes` entry and otherwise
falls back to `proxmox.node`; it never selects a node automatically. GPU VM declarations
use kind `igpu` or `gpu`, mode `dedicated`, one exact PCI identifier, and an existing
`pci_mapping` (defaulting to the declaration key). `kind` defaults to `igpu` for shared
declarations and `gpu` for a bare dedicated declaration; a dedicated iGPU must say
`kind: igpu`, while USB or other entries use `kind: usb` or `kind: other`.

Each attach seam has a matching detach seam — `detach-shared-device.yml`,
`detach-pci-passthrough.yml`, `detach-usb-passthrough.yml` — that also asserts `_+lab`
first, then removes a binding only when its content matches the caller's input AND its
`_.dev+<slug>` tag is present. A content match with no tag is an operator's own entry, left
in place and reported, never deleted. Every other `devN`/`hostpciN`/`usbN` entry, every
other tag, and the guest itself are always untouched.

## Guest application records

`record-app-on-guest.yml` records an application tag and a marker-delimited notes row. The
logical hosting unit depends on the backend:

- A native application records its own LXC.
- A Docker application records its stack host.
- A Kubernetes application records every VM in its cluster because cluster assignment is
  stable while pod placement can change.

The helper at `ansible/files/proxmox/guest-app-record.py` performs an idempotent,
read-modify-write update so one application does not replace another application's record
or operator-owned notes. Recording is bookkeeping and is best-effort; it does not decide
whether deployment succeeded.

Stack creation starts its tag list from authored `config/proxmox.yml` tags or repository
defaults, then adds that stack's Docker, topology and sharing tags. It does not inherit
application or stack tags left in the runtime mapping by an earlier bootstrap play.

For an already affected guest, **Repair Orphan Stack Tag** plans a narrowly scoped repair
through [`repair-orphan-stack-tag.yml`](../../playbooks/maintenance/repair-orphan-stack-tag.yml).
Supply exact reviewed stack LXC VMIDs and the instance; execution requires the literal
confirmation shown by the job. A matching application row in the managed notes refuses
repair. The playbook saves original configs privately on the delegation node, uses PVE's
config digest to reject concurrent changes and verifies every other config field and tag
is preserved. It removes no guest, application record or application data.

**Repair Missing Stack Tag** at
[`repair-missing-stack-tag.yml`](../../playbooks/maintenance/repair-missing-stack-tag.yml)
handles the inverse case: an explicitly reviewed owned guest has no stack identity tag.
Supply its exact VMID, the authored stack name, and the estate when a multi-estate lab
needs one. The playbook derives the tag from repository and lab configuration, requires
the guest name to match that identity, and plans by default. Execution requires the
literal `ADD STACK TAG` confirmation, saves each original config privately, checks the
PVE config digest, and verifies every other config field and tag is preserved. It does
not start or remove a guest.

**Start Owned Guest** at
[`start-owned-guest.yml`](../../playbooks/maintenance/start-owned-guest.yml) plans or
starts one exact `_+lab`-owned stack guest. It checks the VMID, name, type, current state,
and absence of a Proxmox operation lock before delegating to the shared start-and-readiness
task. Planning is the default; execution requires the exact VMID confirmation. It does not
change guest configuration, tags, applications, or data.

## Creation provenance for decommission

New PBS registrations and dedicated bootstrap API roles/users/tokens/ACL edges are
recorded only after successful **new creation** by their owning seam. Existing resources
retain bootstrap reuse behavior and never acquire a retrospective ownership record.
[`lab.py`](../../files/decommission/lab.py) records schema 1 in the creation node's
`/var/lib/homelab-infra/decommission-ownership.json`: `node`, and an `objects` mapping
keyed by `kind:identity`, each containing `source`, `identity`, and `signature`.
The allowed sources are `bootstrap-rundeck` for credentials and `configure-pbs` for the
PBS registration. Resource identity is storage/role/user ID, `user!token`, or the exact
ACL tuple `path|type|principal|role`. Signatures hash the actual resource's non-secret
identity/security/registration fields; no token secret or storage password is recorded.
Backup jobs use their exact repository-authored comment marker; guests use exact `_+lab`
and templates also require `_.template`.

The root-owned regular 0600 node-local record is the authoritative creation linkage.
Root and the reviewed creation seam are the trust boundary: this is not a signed receipt
and cannot establish ownership against a malicious node root or distinguish an identical
replacement root has made under the same resource identity. Do not hand-author records,
copy them between nodes or use names to recover missing stamps. Record failure leaves the
new object unowned for automatic decommission; rerun must not adopt it. Mismatched node,
schema, source, identity or fingerprint refuses ownership; an existing differing stamp
cannot be overwritten. A role signature binds its role ID, excluding privileges that
bootstrap updates on reuse; the original creation stamp remains unchanged. Registration
changes invalidate their stamp. User group membership, unowned
tokens/ACLs, and roles referenced by foreign principals prevent dependent deletion.
ACL and token withdrawal still require their own provenance. Shared/adopted credentials,
unstamped PBS registrations and independent data remain explicit plan exclusions.

See the [maintenance entry point](../../playbooks/maintenance/README.md#whole-lab-decommission)
for operator handoff, volume checks and interrupted-execution recovery.
