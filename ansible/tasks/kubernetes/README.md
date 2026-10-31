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

Each node may set `snapshotter: native` or `snapshotter: overlayfs` in its
`cluster.nodes` entry. Omit the field to use K3s's `overlayfs` default. The role writes an
explicit per-node choice to that node's K3s config and applies it through its serial
configuration restart. Use `native` when that node's overlayfs mounts do not expose valid
image files.

The Kubernetes API, node addresses, and ingress virtual IP remain private. The platform
Caddy is the public TLS edge and proxies a routed application to the stable internal
MetalLB virtual IP. The cluster does not create a second public edge.

### Firewall access

When `firewall.provider: opnsense` is configured, cluster Deploy keeps one host alias named
`homelabinfra_k3s_<cluster>` in sync with every declared node address. Its description is
`homelab-infra: k3s <cluster>`, which is the ownership marker used before any update or
removal. A same-name alias without that marker makes Deploy fail without changing it.
Cluster facts record the alias name for Kubernetes app Deploys.

A Kubernetes app's declared `firewall:` rules use that alias as their egress source, so
traffic from any node can match. The ingress VIP still selects the OPNsense interface and
remains the target for an enabled inbound port forward. Docker, LXC, and VM apps continue
to use their individual guest address. Remove App withdraws the app's rules and leaves the
cluster alias in place.

`Remove k3s Cluster` is a separate, explicitly confirmed destructive action. Remove every
Kubernetes app first; the job refuses while app namespaces or app tags remain. It verifies
each Proxmox guest's declared name, derived VMID and k3s ownership tags before destroying
the node VMs, then removes the owned firewall alias and cluster topology. Destroying the
VM disks also deletes retained local-path volumes. The cluster config, Vaultwarden item and
PBS backups remain as recovery points. While a removal is interrupted, the generated
registry marks it in progress so new Kubernetes app operations stop until Remove finishes.

## Application recovery

Every Kubernetes app backs up and restores through one shared pair:
`recovery-backup.yml` applies `templates/kubernetes/recovery-backup.yaml.j2` (the nightly
CronJob that `Backup App` runs on demand), and `recovery-restore.yml` runs
`templates/kubernetes/recovery-restore.yaml.j2` in plan or restore mode. The shared code owns
the PBS client, its tmpfs cache, the PBS trust ConfigMap, recovery-point identity and
retention, the read-only preview, and the restore order: stage, load the database, then
replace volumes. A role supplies `k8s_recovery` (archives, an optional database step from
`../database/kubernetes-recovery-step.yml`, an optional backup `stop` list, Deployments to
stop and restart during restore, and an optional after-restore task file). When a backup
has a database and a `stop` list, the dump init container quiesces the Deployments before
the database dump; the push container resumes them after archiving the volumes, including
when PBS setup or upload fails after the push container starts. There is a startup gap after
a successful dump: if the Job pod is evicted or lost, or the push image cannot start, no
container remains to resume the Deployments. They stay at zero with the
`backup-quiesced` recovery marker until a later backup attempt reaches its push container
and runs its restart trap. The dump container resumes them itself only when the dump fails.
The database image must support installing Python, and the client image must include Python
for its restart trap. For a filesystem-only archive that
needs quiescing, the push container scales the listed Deployments to zero, waits for their
replica counts to reach zero, and resumes them after success or failure. Set `stop_replicas`
when the app's declared count differs from one; the positive value is also used to resume a
Deployment if an earlier hard-killed backup left it at zero. The Job gets a namespace-scoped
Role limited to those Deployment names. The input contract is in each task file's header.

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

### Eligibility

Eligible applications are Kubernetes candidates with external/disposable state or proven
NFS-safe persistence and restore: currently `searxng`, `homepage`, and `litellm`. `mixpost` may
retain `homelab-local-path` under its existing backup contract. Database HA and storage-heavy rows
(`nextcloud`, `paperless-ngx`, `immich`, `plane`, `n8n`, CRMs, and databases) remain ineligible
until each application proves application-consistent backup and restore. Device-bound, host-path,
media-library, GPU, USB, and high-write workloads retain their declared LXC or VM storage.

The current schema is documented in [`../../vars/CONTRACT.md`](../../vars/CONTRACT.md).
Hosting selection belongs in [`../../playbooks/apps/README.md`](../../playbooks/apps/README.md),
not in this backend guide.
