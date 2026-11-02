# `config/proxmox.yml`

| Key | Required? | Default / notes |
|---|---|---|
| `proxmox.api_host` | required | canonical name |
| `proxmox.api_port` | optional | default `8006` (canonical name) |
| `proxmox.node` | required | default node for database backend instances without classed placement; an instance may override it in `config/apps/<instance>.yml` |
| `proxmox.api_user` | required | |
| `proxmox.api_token_id` | required | |
| `proxmox.api_token_secret` | required *in file or runtime environment* | secret — omit it from the file after Vaultwarden cutover |
| `proxmox.validate_certs` | optional | default `false` — a stock Proxmox node is self-signed, so guest creation fails with `CERTIFICATE_VERIFY_FAILED` when this verifies. `inventory/proxmox.yml` assumes the same. Set `true` once the node serves a trusted certificate |
| `proxmox.nodes` | optional on one node | `{node_name: address}` for every cluster node used as a guest target, written by `bootstrap-rundeck.sh` from `pvesh get /cluster/status`. Consumed by `tasks/proxmox/register-nodes.yml`, which makes `delegate_to: <node name>` resolvable — node names have no `ansible_host` from the dynamic inventory. `proxmox.api_host` is the fallback for `proxmox.node`; a per-instance node override must have its own entry |
| `proxmox.storage_nodes` | optional | list of nodes that hold an NFS export protected by the host-firewall baseline. When absent, the baseline targets `node_roles.storage`, or `proxmox.node` when no storage role is configured. |
| `proxmox.firewall.enabled` | optional, default `false` | `true` opts the maintenance playbook `playbooks/maintenance/reconcile-proxmox-host-firewall.yml` into the datacenter and host baseline. Unset/false does not change datacenter options or host rules beyond existing per-app rules. |
| `proxmox.firewall.management_sources` | required when enabled | CIDRs allowed to reach SSH TCP 22 and the Proxmox API TCP 8006 on each node. |
| `proxmox.firewall.storage_clients` | optional | CIDRs allowed to reach NFS TCP 111 and 2049 on `proxmox.storage_nodes`; when empty, the baseline adds no NFS rules and does not require compute-node NFS mounts during post-change verification. Use `/32` or `/128` entries for legacy host-firewall peer rules to be identified by their exact source hosts. |
| `proxmox.storage` | required *in practice* | default storage for cloud-init templates, database backend instances without classed placement and explicit non-classed paths; classed guests use `storage_ids` |
| `proxmox.node_roles` | required for classed multi-node placement | `{storage: <node>, compute: [<node>, ...]}`. Classed database VMs pin to `storage`; unpinned floating VMs/LXCs choose the online compute node with the most available memory after storage and active-bridge checks. List order breaks ties; compute nodes exclude the storage node. Database LXCs follow the floating-LXC placement class unless their instance config pins a node |
| `proxmox.storage_ids` | required for classed guests | `{database_vm, floating_vm, floating_lxc}` storage IDs. Database VMs default to `database_vm`, database LXCs to `floating_lxc`, and other classed VMs/LXCs use the IDs declared here |
| `proxmox.floating_vm_cpu_model` | required for floating/GPU VMs | common migratable CPU model; `host` is rejected |
| `proxmox.devices` | optional by default; required by an attachment request | map of `<physical-device-name>: {mode: shared\|dedicated, identifiers: [...]}`. GPU VMs require `kind: igpu|gpu`, `mode: dedicated`, exactly one PCI identifier, an explicit `node`, and an existing PCI resource mapping (`pci_mapping`, defaulting to the declaration key). For other PCI/USB mappings and shared host paths, placement resolves the eligible node set from the resource mapping or path presence; an optional `node` restricts that set. `kind` defaults to `igpu` for shared and `gpu` for bare dedicated declarations; a dedicated iGPU must say `kind: igpu`. Optional `stack` names the logical Docker stack whose VM owns the dedicated device; it defaults to the device key. |
| `networks.<name>.cidr` | required | per named subnet, **after inheritance** — see below |
| `networks.<name>.gateway` | required | per named subnet, after inheritance |
| `networks.<name>.dns_servers` | required | per named subnet, after inheritance |
| `networks.<name>.bridge` | required | per named subnet, after inheritance |
| `networks.<name>.vlan` | optional | Proxmox VLAN tag; `0` or absent means untagged. One network per VLAN — the tag, subnet and gateway travel together, so an app changes VLAN by changing its `proxmox.network` name |
| `networks.<name>.reserved` | optional | list of spans never allocated: an address, `"a-b"`, or a CIDR. Independent of Proxmox — this is how a NAS, a switch or a router's DHCP range stays unallocatable |
| `networks.<name>.pools` | optional | `{<pool>: {range: "a-b"}}` or `{<pool>: {cidr: "..."}}`; each pool must sit inside `cidr`. A guest allocated into a pool never lands outside it, and an exhausted pool fails the run rather than spilling into the wider subnet |
| `networks.<name>.default_pool` | optional | pool used by a guest that names none and inherits none |
| `networks.<name>.ip_offset` | optional | fallback walk only (no pool applies). An index into the host range, not a last octet: at a /20, `10` is `x.0.10` and the range runs to `x.15.254` |
| `networks.<name>.max_hosts` | optional | fallback walk only: how far past `ip_offset` allocation may walk |

**Every named network inherits `networks.default`.** `tasks/network/generate-ip.yml` builds
the effective config as `networks.default | combine(networks.<selected>, recursive=True)`,
and `config-doctor.sh` requires the four keys above on that merged result. A band therefore
declares only what differs from `default` — legitimately one key.

That is what lets the estate split start before the VLANs do. `bootstrap-rundeck.sh` asks
for two ADDRESSES — where lab-wide services start and where the estate's apps start — and
writes `shared` and `<estate>` as offsets into one flat subnet, with `max_hosts` on the
lower band so allocation cannot walk into the upper one. Both sets are addressed apart from
the first deploy while sharing one bridge and one broadcast domain; segmenting later adds
`cidr`/`gateway`/`vlan` to a band that already exists, and **no guest already deployed
changes address**. Deferring the split instead means renumbering the whole lab.

Pool selection, highest first: `proxmox.pool` in the instance file (must exist, or the
run fails); the pool named after the app's `stack`, used only if the network defines it;
`default_pool`; otherwise the `ip_offset`/`max_hosts` walk. `proxmox.ip_address` in an
instance file pins an exact address — honoured as written or refused with the conflict
named, never quietly replaced. `ansible/scripts/allocate-ip.py` decides; every refusal
carries its reason.

**Network selection is by SCOPE, not by a name repeated in every instance file.**
`ansible/tasks/network/resolve-network.yml` is the one seam, and it answers in this order:

1. `proxmox.network` in the instance file, or `stacks.<name>.network` for a stack host —
   an explicit choice, which must name a declared network or the run fails.
2. `shared`, when the application is `scope: lab` in `catalog/applications.yml` or its
   stack declares `shared: true`.
3. The app's estate: `domains.<estate>.network` when that estate declares one, otherwise a
   network named after the estate itself.
4. `default`.

Steps 2 and 3 are ADVISORY — used only when `config/proxmox.yml` actually declares a
network of that name, the same asymmetry `pool_hint` uses. A lab with one flat `default`
network resolves to `default`. A lab segments one band at a time by declaring
`networks.shared`, then `networks.<estate>`, and
redeploying. No `vars/app-defaults/*.yml` pins `network:` — a name in git-managed defaults
would override this resolution in every lab at once.

The estate boundary is a network boundary because one network is one VLAN: the tag, the
subnet and the gateway travel together. What this platform does NOT do is create that VLAN,
its bridge, its routes, or the rules that let `shared` reach each estate. Those are
the operator's, exactly like the gateway and the DHCP range. An application's own
internet egress and port forwards are different: the app declares them under `firewall:`
and Deploy opens them through `infrastructure.firewall`. Declare here only what already
exists on the wire.
| `ansible.ssh_user` | required | |
| `ansible.ssh_public_key` | required | |

## Database backend instance placement

PostgreSQL, Redis, MariaDB, MySQL and InfluxDB instances may set these keys in
`config/apps/<instance>.yml`:

| Key | Required? | Default / notes |
|---|---|---|
| `proxmox.guest_type` | optional | `lxc` by default; choose `vm` or `lxc` per instance. Existing PostgreSQL and Redis configs may keep using `proxmox.type` when `guest_type` is absent. |
| `proxmox.node` | optional | node for this instance; defaults to `config/proxmox.yml` `proxmox.node` without classed placement. Classed VMs default to `node_roles.storage`; classed LXCs use the eligible compute nodes unless an instance node is set |
| `proxmox.storage` | optional | guest storage for this instance; defaults to `config/proxmox.yml` `proxmox.storage` without classed placement. Classed VMs default to `storage_ids.database_vm`, and classed LXCs to `storage_ids.floating_lxc`. A VM's cloud template uses this storage too unless `proxmox.cloud_template.storage` is set. |

Existing type-specific placement keys remain valid and take precedence: LXC
`proxmox.lxc.node` and `proxmox.disk_volume.storage` (or
`proxmox.lxc.disk_volume.storage`), and VM `proxmox.vm.node` and
`proxmox.vm.storage`. Deploy fails before starting or changing a tagged existing guest
when its Proxmox type differs from `guest_type`; guests are never converted.
InfluxDB reuses a matching lab-owned LXC or VM left by Remove App. Remove App can also
destroy either native guest type when `destroy_guest: true` is paired with
`delete_data: true` and the ownership checks pass.
