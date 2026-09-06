# Kubernetes backend

This directory contains shared tasks used by applications hosted on the k3s cluster. The
cluster itself is built by `ansible/playbooks/apps/k3s-cluster.yml` and configured by the
`k3s_cluster` role.

## Boundary

- The k3s cluster is a hosting backend. It is not an application deployment.
- One application instance owns one namespace named `app-<instance>`.
- Deploy, status, backup, restore, and removal use that namespace as the ownership boundary.
- An existing namespace without the homelab-infra ownership label is not adopted.
- Runtime secrets are copied from canonical Vaultwarden items into only the owning
  namespace.

`derive-namespace.yml` owns instance-to-namespace conversion. Consumers must use it rather
than construct namespace names independently.

## Cluster and publishing flow

The cluster playbook provisions each VM through the shared Proxmox clone seam. Its
sequential cluster-foundation play ensures one node initializes embedded etcd and later
nodes join it. Cluster-scoped storage and ingress configuration runs only after all nodes
have joined.

The Kubernetes API, node addresses, and ingress virtual IP remain private. The platform
Caddy is the public TLS edge and proxies a routed application to the stable internal
MetalLB virtual IP. The cluster does not create a second public edge.

## Availability and storage

Read availability from the declared properties, not from the node count:

- `failure_domain_mode` describes whether control-plane members occupy distinct Proxmox
  nodes.
- `storage_class` describes where application volumes live and how they can move.

The current default StorageClass is `homelab-local-path`. The platform owns its local-path
provisioner and disables the bundled k3s local-storage add-on so exactly one default class
exists. Each volume is a directory on one cluster VM. A pod with that volume is pinned to
the node; if the node is unavailable, the pod remains `Pending`. Control-plane quorum does
not make application data highly available.

### Opt-in shared NFS storage

`shared_storage.enabled: true` installs the pinned upstream NFS CSI chart and creates the
non-default `shared_storage.class` and `shared_storage.snapshot.class`. The NFS CSI driver
creates one directory per PV beneath the named NFSv4 export. It supports `ReadWriteOnce`,
`ReadWriteMany`, and `ReadOnlyMany` PVC access modes. The class is never default: existing
PVCs cannot change class, and new workloads must name it explicitly.

The NFS target is outside homelab-infra authority. Before enabling it, the target owner must
approve the named disposable export and declare `capacity_owner` and `failure_domains` in
`config/apps/k3s-cluster.yml`. PVC size requests are not NFS quotas. Capacity, export quotas,
filesystem snapshots, replication, replacement hardware and NFS-service recovery belong to the
target owner. `Retain` is mandatory for PVs and snapshot artifacts. The role never creates,
formats, exports, resizes, snapshots, restores, or deletes the target.

The CSI controller has two hostname anti-affined replicas. After one Kubernetes-node loss, a
workload can mount a shared volume after rescheduling only while the NFS service, target storage,
and network route remain available. Otherwise recover through the target owner's NFS-service or
filesystem recovery path, or through the application-consistent backup. Shared storage alone does
not make a single-replica application or database highly available.

CSI `VolumeSnapshot` artifacts are filesystem crash-consistent, not application-consistent
backups. A database, queue, or application with durable state may use this class only after its
own quiesce/dump, backup, restore, and validation contract is implemented. PBS VM backups remain
node-rebuild artifacts, not a substitute for application recovery.

### Migration and rollback

Do not edit or delete an existing PVC, PV, or StorageClass. Kubernetes cannot change a bound PVC's
StorageClass. For one approved application instance:

1. Verify its NFS access mode and successful application-consistent restore point outside the source PVC.
2. Deploy `k3s-cluster.yml` with shared storage enabled. Verify CSI controller/node-plugin readiness,
   a non-default NFS StorageClass, and the snapshot class.
3. Quiesce writes. Create a new PVC naming `shared_storage.class`; copy only through the documented
   application-consistency procedure. Never copy a live database directory as migration.
4. Validate a separately named disposable restore/migration workload before changing production.
5. Keep the source PVC and restore point. Roll back by returning to the unchanged source PVC, then
   restore the verified artifact if writes reached the new volume. Do not delete either PVC without
   explicit target approval.

Turning `shared_storage.enabled` off does not remove the driver, classes, snapshots, or data.
Explicit removal is separate maintenance after every dependent PV, PVC, and snapshot is inventoried.

### Eligibility and required live drill

Eligible Batch C candidates are Kubernetes candidates with external/disposable state or proven
NFS-safe persistence and restore: currently `searxng`, `homepage`, and `litellm`. `mixpost` may
retain `homelab-local-path` under its existing backup contract. Database HA and storage-heavy rows
(`nextcloud`, `paperless-ngx`, `immich`, `plane`, `n8n`, CRMs, and databases) remain ineligible
until each application proves application-consistent backup and restore. Device-bound, host-path,
media-library, GPU, USB, and high-write workloads retain their declared LXC or VM storage.

Record this live drill only against the named disposable workload and approved target:

1. Provision a `ReadWriteMany` PVC. Record PV path, access mode, free target capacity, CSI status,
   and target snapshot/backup identifier.
2. Write a known checksum, take a CSI snapshot, restore it to a new disposable PVC, and record its checksum.
3. Stop one Kubernetes node in a declared Proxmox failure domain. Record Ready nodes, CSI availability,
   pod rescheduling or its defined recovery behavior, and checksum after recovery.
4. Restart the node and confirm convergence. Remove only the named disposable namespace after target-owner
   approval; retain snapshot and restore point until restore acceptance.

The current schema is documented in [`../../vars/CONTRACT.md`](../../vars/CONTRACT.md).
Hosting selection belongs in [`../../playbooks/apps/README.md`](../../playbooks/apps/README.md),
not in this backend guide.
