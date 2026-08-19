# 204 — Notes

## 2026-08-18 — Slice cut

### Why now

The foundation is stable, the repository work queue was empty, and the next application
catalog is large enough that a common orchestrated backend can remove repeated deployment
work. Kubernetes enters before that catalog is implemented, but it is an additional
hosting backend rather than a migration program.

Foxglove is the first consumer because external accessibility and business recovery are
the reason to build the backend. Kubernetes does not itself create accessibility. Public
DNS, the external network path, Caddy and Authentik remain responsible for who can reach an
application.

An access class is not an Authentik mode. The access class says which network path may
publish an application. `routing.identity` says where authentication occurs. Keeping those
decisions separate prevents `catalog` from being mistaken for an access control and
prevents a private application from being required to publish a public DNS record merely
to satisfy acceptance.

### Workload boundary

Good first candidates are standard OCI workloads with conventional HTTP ingress, limited
hardware coupling, and state that can be restored independently. Keep these outside the
cluster until a later explicit decision:

- Caddy, Vaultwarden, Authentik, the runner and Proxmox/PBS control services
- working applications whose migration has no user-visible benefit
- large media data paths and hardware-bound GPU or kernel-module workloads
- stateful applications without an application-consistent backup and restore method

The pilot should be useful to Foxglove but low-risk. It must exercise the complete path,
including external access and recovery, before the app catalog adopts the backend.

### State boundary

k3s can reconstruct cluster services without reconstructing application data. The first
implementation therefore needs an explicit storage decision before it selects a pilot:
where persistent volumes live, what happens when their node is unavailable, and which
operation deletes them. The implementation must not describe a multi-node control plane as
application-highly-available when the selected volume remains tied to one failed node.

Backups protect application state, not only VM disks. A database dump or a tool-supported
quiesce must produce the application-consistent artifact, and the restore proof must start
with a fresh namespace so surviving cluster objects cannot make the result look healthier
than the backup is.

### Credential boundary

The post-cutover source of truth remains Vaultwarden. Runtime kubeconfig files and join
material may persist at protected k3s-managed node paths or exist temporarily where a job
requires them. “Control-plane storage” describes that runtime placement; it is not a
second canonical secret store. The same distinction applies to application credentials:
Kubernetes Secrets are runtime deployment objects, while Vaultwarden remains canonical.

### Close plan

1. Decide topology, failure domains, ingress address, access-class field, persistent
   storage and secret ownership.
2. Provision a fresh cluster and prove an idempotent cluster re-run and the declared
   ingress-address behavior.
3. Add the Kubernetes application adapter and owned-resource model behind the existing
   app/job contract.
4. Add status, removal, notification, registry and guest-record behavior before treating
   the adapter as a complete hosting backend.
5. Deploy and wire one Foxglove pilot through the existing estate and its selected access
   class.
6. Prove explicit data preservation and deletion, application-consistent restore, and the
   declared node-failure behavior.
7. Use the proven adapter for suitable entries in `408-app-catalog` when that slice lands
   in this checkout.

### Later niceties — recorded, not required by 204

Consider these only after the vertical slice is live and recoverable:

- GitOps reconciliation with Flux or Argo CD if click-driven Ansible deployment becomes a
  real operational limit
- automated, tested k3s upgrade waves and a separate staging cluster
- Horizontal Pod Autoscaling, KEDA and capacity-aware worker provisioning
- richer default-deny network policies, admission policy, image signing and software bill
  of materials enforcement
- External Secrets-style continuous synchronization if Vaultwarden gains a suitable safe
  integration; the first implementation can inject runtime values from the existing
  control-plane secret path
- cluster-native logs and traces, expanded dashboards and per-application service-level
  objectives beyond the existing Uptime Kuma availability check
- a service mesh, only if measured service-to-service identity or traffic-control needs
  justify its control-plane cost
- public DNS automation after the external exposure model is proven; DNS-01 certificate
  credentials alone do not imply permission to publish application records
- automated certificate management inside the cluster if internal TLS becomes necessary;
  the initial public TLS boundary remains Caddy
- multi-cluster disaster recovery, off-site restore drills and workload promotion between
  clusters
- tenant quotas, namespace self-service and delegated Foxglove deployment access

Each nicety needs a new queue row or slice when selected. This list is not hidden acceptance
scope for 204.

## 2026-08-18 — Decisions (close-plan step 1)

Taken with the operator before any code. Each one closes a question the README's first
`Remaining` row left open. No row below is re-decidable by an implementer of a later row.

### Distribution — k3s, hosting kind named `kubernetes`

k3s is a CNCF-conformant Kubernetes distribution, not a fork or a subset: the same
manifests, Helm charts and `kubectl` apply. It differs in packaging (one binary, one
systemd unit), datastore (embedded etcd rather than a separate cluster) and in the
components it bundles. The hosting kind is therefore named `kubernetes`, not `k3s` — the
adapter talks to a conformant API and the distribution stays swappable.

### Topology — three servers, embedded etcd, one per physical node

| Node | VM | Size | Role |
|---|---|---|---|
| pve-host-1 | k3s-1 | 2 GB / 2 vCPU | server, **tainted NoSchedule** — quorum only |
| pve-host-2 | k3s-2 | 16 GB / 4 vCPU | server + workloads |
| pve-host-3 | k3s-3 | 12 GB / 4 vCPU | server + workloads |

Three distinct physical failure domains, so the README's condition for calling the control
plane highly available is met and the UI may say so. etcd quorum is 2 of 3: the control
plane tolerates the loss of any one node.

**Why host-1's node is small and tainted.** host-1 has 15.8 GB and runs the CARP BACKUP
peer of the lab firewall (VM 13003, real address 192.168.13.3; `192.168.13.1` is the VIP).
Measured commitment before this slice was 14.7 GB — OPNsense 6,144 MB, `civic-dc-2`
4,096 MB, ZFS ARC 1,577 MB, four LXCs 1,393 MB, PVE daemons ~1,462 MB.

Two reclaims precede provisioning: `civic-dc-2` drops 4 GB → 2 GB (its own balloon driver
reports a 940 MB working set) and ZFS `c_max` drops to 1 GB. That frees ~2.6 GB, of which
the quorum node takes 2 GB.

OPNsense 13003 **stays at 6 GB**. Raising it looked necessary because host RSS tracked
every increase, but the cause is `balloon: 0` — the allocation is pinned on day one
regardless of guest demand. The same fact is what makes a co-resident k3s node safe: a CARP
failover consumes no additional host memory, because the firewall's 6 GB is already
resident. The quorum node competes for CPU only, and etcd plus kubelet on a tainted node is
near-idle. Sizing 13003 against the MASTER's real ~4.3 GB working set is firewall capacity
work, not this slice.

### Ingress — keep Traefik, disable ServiceLB, add MetalLB

`--disable=servicelb`, Traefik retained, MetalLB in L2 mode supplying one floating LAN VIP.

Traefik is the in-cluster ingress controller; without an ingress controller an `Ingress`
resource means nothing, so disabling it with no replacement would contradict this slice's
own requirement for one stable internal ingress address. Keeping the bundled controller
costs no provisioning work. It terminates plain HTTP on a private address only.

ServiceLB (klipper-lb) is replaced rather than kept because it binds a port on *every*
node's address, which yields three addresses, none of which move on failure. MetalLB gives
one VIP that a surviving node claims within seconds — the declared answer to "how the
ingress address is owned or moved when a node fails". The platform Caddy points at that VIP
permanently and remains the sole public TLS edge.

### Persistent storage — `local-path` now, Longhorn as a follow-on slice

Default StorageClass is k3s's bundled `local-path`, backed by SSD-backed ZFS. A PVC is a
directory on one node's disk and pins its pod to that node: losing the node leaves the pod
`Pending` until the node returns. That is recorded plainly and the UI must not describe such
a workload as application-highly-available.

`delete_data: true` on removal deletes the instance's PVCs; `delete_data: false` retains
them.

Longhorn is wanted soon and gets its own queue row. It buys *availability* — a pod
reschedules onto a surviving node with its data already replicated — which is a different
problem from backup and does not substitute for the restore proof below.

### Backup — `proxmox-backup-client` CronJob into the existing PBS datastore

PBS continues to snapshot the k3s VMs; that is the node rebuild path and nothing more. A
whole-VM image is crash-consistent only and cannot see inside a PVC, so it does not satisfy
this slice's acceptance.

The application-consistent artifact is produced in-cluster: a Kubernetes CronJob runs a
quiesced database dump and pushes it, with `proxmox-backup-client`, straight into the PBS
datastore the lab already runs. One destination, one retention policy, one restore tool the
operator already knows. Restore proof starts from a fresh namespace.

### Application database — in-cluster, namespace-scoped

MySQL and Redis run as pods in the application's own namespace, owned by the instance and
deleted with it. This keeps the slice self-contained and is what exercises the owned-resource
and `delete_data` contracts. It does not pre-empt slice 408 Batch B's standalone
`postgresql` / `mariadb` LXC rows: an application may later name an external backend
instance instead.

### Pilot — `mixpost`, with `hi-events` as the second consumer

`mixpost` is the pilot: MySQL plus Redis, plain HTTP ingress, its own login, and no
dependency on anything undecided. It exercises the full contract — a database dump, a real
PVC, estate wiring and removal.

`hi-events` follows in a separate slice. It is the application that genuinely needs the
`public` access class, and it forces slice 408's unresolved SMTP contract. Sequential, not
simultaneous: running both at once means a green result cannot say which half worked.
