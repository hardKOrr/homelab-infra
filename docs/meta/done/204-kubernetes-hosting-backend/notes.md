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

## 2026-08-18 — Cluster built and proven (close-plan step 2)

Rundeck executions 213–218 against the live lab. The cluster is up; five defects were
found by running it, and none of them were reachable by either gate.

### What the run proves

Three nodes Ready on `v1.31.5+k3s1`, one per physical Proxmox node, all three reporting
`EtcdIsVoter=True` — so quorum is 2 of 3 and the control plane tolerates one node loss
across three real failure domains. `k3s-1` carries `homelab-infra.io/quorum-only:NoSchedule`
and the other two carry none.

`homelab-local-path` is the default StorageClass with `reclaimPolicy: Retain`; the bundled
`local-path` remains, demoted, on `Delete`. Traefik holds the declared VIP `192.168.0.30`,
answers HTTP 404 (no Ingress yet — the correct answer), and ARP resolves it to a live
speaker. `kube-system` has no DaemonSets at all, so ServiceLB is genuinely gone. MetalLB
speakers run on `k3s-2` and `k3s-3` only, because `k3s-1`'s taint excludes it.

Execution 218 re-ran the job unchanged: `changed=0` on all three cluster nodes. The six
remaining `changed` entries are inside the shared `tasks/proxmox/vm-clone.yml` — the
`proxmox_kvm` update call and `qm resize` both self-report changed against a converged
guest. Pre-existing, non-mutating, and already true for PBS, so it is recorded here rather
than fixed inside this slice.

### The five defects, and why the gates could not have caught them

1. **jsonpath quotes eaten by `command:`.** `jsonpath={...[?(@.type=="Ready")]...}` written
   as a `cmd` string is split on whitespace with its quotes dropped, so kubectl received
   `@.type==Ready` and answered `unrecognized identifier Ready` — 36 retries against a node
   that had been Ready since the first attempt. Every jsonpath in the role is `argv` now,
   including the ones containing no quotes, so the next one added by copying a neighbour
   inherits the safe shape.

2. **dpkg lock race.** A freshly booted Debian cloud image runs its own `apt-get` for the
   first minute of its life. One guest at a time wins that race by luck; three at once lose
   it, and `k3s-3` took the whole build down over a lock that would have cleared in twenty
   seconds. `guest-bootstrap.yml` now sets `lock_timeout` and retries — a fix that helps
   every app, not only this one.

3. **JSON payload turned into a Python dict repr.** Ansible converts a rendered template
   that parses as a data structure back into one and stringifies it with `repr`, so a
   literal `{"reclaimPolicy":"Retain"}` reached the API server as
   `{'reclaimPolicy': 'Retain'}`. Build the dict and pass it through `to_json`. The same
   trap is documented at length in `tasks/bitwarden/upsert-item.yml`, which hit it first.

4. **`reclaimPolicy` is immutable.** Defects 3 and its predecessor were both fixing the
   delivery of a request that could never succeed: Kubernetes refuses
   `updates to reclaimPolicy are forbidden` on an existing StorageClass. The platform now
   declares its own class with the same provisioner, which is the better shape anyway — a
   bundled class we had edited would be reverted by a future k3s upgrade, silently, with
   nothing failing at the time.

5. **Cluster-scoped workloads applied before the cluster had workers.** Storage and MetalLB
   ran from inside the `serial: 1` install play on the founding node, which reads like the
   natural home for cluster-scoped objects. But inside that play the founder is by
   definition the only node yet in existence — and here the founder is the quorum-only node
   with a NoSchedule taint. MetalLB's controller Deployment had nowhere to go. Nothing was
   misconfigured; a one-node cluster was asked to run a workload before its workers joined.
   Both now run from a Configure play after every node is Ready.

Defect 5 is the one worth remembering. The taint did exactly what it was asked to do, and
the design was still wrong, because "apply cluster-scoped things on the founder" quietly
assumed the founder could run a pod.

## 2026-08-19 — Pilot deployed and removed (close-plan step 3)

Rundeck executions 219–229 against the live cluster. `mixpost` deployed into
`app-mixpost`, served, and was removed twice — once by each `delete_data` path. Four more
defects were found, all of them by running the thing.

### What the runs prove

`delete_data: false` (execution 229): the namespace is gone, both PersistentVolumes are
`Available` with `claimRef` cleared, and 199 MB of application data is still on `k3s-3`.
That is the restore point the retain path promises.

`delete_data: true` (execution 227): the namespace is gone, both PersistentVolumes are
gone, their directories are gone from the node, and 211 MB came back.

### The four defects

1. **The readiness probe could never pass.** `httpGet: /` — the application answers `302`
   to `APP_URL + /mixpost`, `APP_URL` is `https`, and kubelet follows redirects, so the
   prober re-requested `https://<pod-ip>:80/mixpost`: TLS against a plaintext port. It
   failed 582 times over 91 minutes while the application answered `200` to any plain
   request. The probe now asks for `/mixpost/login`. Nothing was wrong with the
   application or with the manifest's syntax — only with the interaction between the
   application's redirect and the prober's redirect-following.

2. **The retaining path's `claimRef` patch was undone as it was written.** It ran while the
   PVC still existed, so the PV controller wrote the reference straight back: the task
   reported `changed` on every run and left volumes `Released` holding a stale claim — the
   exact state its own comment said it prevented. The comment's premise was false too:
   under `Retain` the PVs outlive the namespace, which the run demonstrated. The patch now
   runs after the namespace deletion.

3. **`delete_data: true` deleted the records, not the data.** Found by reading, before
   running. Under `Retain` the local-path provisioner never runs its cleanup pod, so
   deleting the PVCs removed the PV objects and left every byte on the node. Removal now
   flips each volume to `Delete` before the namespace goes.

4. **A gate run could hang indefinitely.** `ansible-lint` executes files in the tree as
   inventory scripts; `webui-password.py` reads stdin by design and blocked for 9 h 24 m at
   0.00 s CPU. Both gates now `exec < /dev/null`. Verified after the fix: 6 m 45 s for both
   gates together.

Defects 2 and 3 have the same shape as the cluster build's defect 3 — code whose comment
asserts behavior the code cannot produce, passing every gate.

## 2026-08-20 — Live audit of the cluster, with the pilot removed

Read-only check through the Proxmox guest agent, cluster clock 00:12 UTC.

Healthy: three nodes `Ready` on `v1.31.5+k3s1`, all three `EtcdIsVoter=True`, `k3s-1`
still carrying `homelab-infra.io/quorum-only:NoSchedule` and the other two clean. Traefik
holds `192.168.0.30` as its `LoadBalancer` address, MetalLB speakers on the two untainted
nodes only, `kube-system` free of DaemonSets. Node load is nil (`k3s-1` 927 MiB of 2 GB,
the workload nodes under 11 %).

Not serving anything: no `Ingress` object anywhere, no application namespace, no CronJob,
and no route on the platform Caddy pointing at the VIP. The pilot is removed, so the
cluster is idle infrastructure rather than a hosting backend in use.

Left on the node: four orphan PersistentVolumes, ~400 MB on `k3s-3`. Two are `Available`
with no claim — the legitimate restore point from execution 229. Two are `Released`
holding stale `app-mixpost/*` claims — litter from the runs before defect 2 was fixed.

### New finding — the bundled StorageClass is default again

Both `homelab-local-path` and k3s's bundled `local-path` currently carry
`storageclass.kubernetes.io/is-default-class: "true"`.

`storage.yml` demotes the bundled class and the code is correct, but the demotion does not
survive: k3s's deploy controller re-applies its packaged addon manifest
`/var/lib/rancher/k3s/server/manifests/local-storage.yaml` on every start of the `k3s`
unit, and line 96 of that file sets the annotation back to `"true"`. The nodes have
restarted since the last converge, so the patch is gone. An Ansible re-run demotes it
again, and reports `changed` every time.

No volume landed on the wrong class. With two defaults the `DefaultStorageClass` admission
plugin picks the newest by `creationTimestamp`, and `homelab-local-path`
(`01:09:32Z`) is newer than the bundled class (`00:55:46Z`). That is an accident of build
order, not a guarantee: a k3s upgrade that recreates the bundled class makes it the newer
one, and every subsequent PVC that names no class silently gets `Delete` reclaim on a
class this platform never described.

This is the same trap the storage decision already named — "a bundled class we had edited
would be reverted by a future k3s upgrade, silently, with nothing failing at the time" —
except the trigger is a service restart, not an upgrade. The durable fix belongs to the
open persistent-storage row: stop editing the bundled addon at all. Either shadow the
manifest with a `local-storage.yaml.skip` file and ship the provisioner ourselves, or start
the servers with `--disable=local-storage` and own both the provisioner Deployment and the
class. Both need a cluster re-converge, so neither was applied during this read-only check.

## 2026-08-20 — Fresh-cluster acceptance and closure

Executions 234–253 completed the remaining live path. Execution 234 reimported the jobs;
235 established the clean baseline; 236 and 237 proved report and guarded destructive
deletion of one retained volume. Execution 238 then destroyed and rebuilt all three k3s VMs
from their declared configuration. Execution 239 deployed Mixpost through the Foxglove
Caddy route, and a disposable account created through Mixpost's own model authenticated on
the source application. Execution 242 was the converged deploy: `ok=162 changed=1 failed=0`,
where the one change was the corrected backup Secret rather than a workload or wiring
change.

### Backup and restore

Execution 243 created PBS snapshot `host/mixpost/2026-08-20T18:13:26Z`. It contains
`database.pxar`, `storage.pxar` and `index.json`. Execution 245 listed it without changing
the target. Execution 246 read the named snapshot's index and described the replacement
with `overwrite=false`. Execution 249 restored it into the separately deployed
`app-mixpost-restore` namespace, loaded a 21,557-byte SQL dump, reported database row and
storage counts, carried the source `APP_KEY` into the target's canonical Vaultwarden item
and runtime Secret, cleared Redis and completed with `ok=90 changed=7 failed=0`.

The artifact proof was followed by an application proof. The source account's random
password stayed in a root-only temporary file on `k3s-1`; it never entered a job argument or
log. The same credentials submitted through the restored application's real CSRF-protected
login form reached `/mixpost`, and the restored profile name was visible. The check passed
again after the node-loss recovery below.

Four failures made the final restore trustworthy rather than merely green:

1. Execution 240 failed before PBS because the backup Secret's `my.cnf` held literal `\n`
   characters. Commit `68d3bd2` renders both backup and restore files as YAML literal
   blocks. Execution 241 correctly still failed against the deploy-time Secret from 239;
   execution 242 refreshed it before the successful backup.
2. Execution 247 reached PBS but restore failed with `EOPNOTSUPP` before it cleared target
   storage. `proxmox-backup-client` opens an unnamed temporary file beneath `/tmp`; the
   container image's overlay filesystem does not implement that operation. Commit
   `9008494` tested an ACL/xattr hypothesis, and execution 248 disproved it with the same
   errno. Commit `eb9dac3` restored normal metadata handling and mounted a dedicated
   memory-backed `emptyDir` at `/tmp`; execution 249 passed.
3. A failed restore deliberately leaves the web deployment at zero replicas. The successful
   retry then read that safety state as its desired state and finished green but unavailable.
   Mixpost's manifest declares exactly one web replica, so commit `ff5f5e0` makes successful
   restore return to one and keeps only the rescue path at zero. Execution 250 reconverged
   the already-restored target and the HTTPS/application checks passed.
4. These are live-runtime contracts. Static rendering, server-side dry-run, lint and syntax
   checks could validate none of the three interactions above: Ansible native-value string
   conversion, `O_TMPFILE` on a container overlay, or a retry beginning from deliberate
   safety state.

### One-node loss

Before the loss, both database pods and PVs were on `k3s-2`; both web pods and their storage
PVs were on `k3s-3`. MetalLB speakers ran on the two workload nodes. VM `168000023`
(`k3s-3` on `pve-host-3`) was stopped abruptly.

The API remained responsive and `k3s-1` plus `k3s-2` stayed Ready. `k3s-3` transitioned to
NotReady, the surviving MetalLB speaker remained on `k3s-2`, and both HTTPS routes returned
502. At `2026-08-20T19:06:02Z`, after the five-minute NoExecute toleration, Kubernetes
evicted the restored web pod. Its replacement stayed Pending. The scheduler's exact
constraints were one quorum-only node, one unreachable node and one `volume node affinity
conflict`. The control plane therefore tolerated the loss while application data correctly
did not move.

Starting only VM `168000023` returned `k3s-3` to Ready. Both web pods attached to their
original node-pinned volumes and passed readiness; Redis rescheduled onto `k3s-2`; both
MetalLB speakers were healthy. The restored application login and profile check passed
after recovery.

### Cleanup and final state

Executions 251 and 252 removed `mixpost-restore` and `mixpost` with `delete_data=true`,
including their platform wiring and four PVs. The disposable password file was then deleted
from `k3s-1`. The source PBS snapshot remains as the proved recovery artifact; project
config and canonical Vaultwarden items remain under the repository's restore-point policy.

Execution 253 was the final read-only status: all three VMs running, all three Kubernetes
nodes Ready, no application namespaces, no retained PVs, one default
`homelab-local-path` StorageClass with `Retain`, and `changed=0 failed=0`. Both full WSL
gates passed after each final restore fix and before closure.

## 2026-08-21 — Re-proving the backend after a review pass, executions 254–267

The slice closed on 2026-08-20 with a clean cluster. A review pass the next morning then
changed nine files across this exact path — `provision-node.yml`, the k3s role,
`derive-namespace.yml`, the Mixpost role's backup and restore tasks, both maintenance
playbooks, `reclaim-volume.yml` and a new 108-line backup-freshness section in
`status.yml` — and not one of them had been executed. Under this repository's own standing
rule that is nine files of presumed-broken code sitting on top of a closed acceptance, so
the whole cycle was run again against them.

| Execution | Job | Result |
|---|---|---|
| 254 | Lab Status | Baseline: three nodes Ready, no namespaces, no retained volumes |
| 255 | Deploy k3s Cluster | `changed=0` on all three nodes; the six `localhost` changes are the documented `vm-clone.yml` baseline |
| 256 | Deploy Mixpost | Fresh deploy through the Foxglove route, backup CronJob applied, no degradation |
| 257 | Backup App | PBS snapshot `host/mixpost/2026-08-21T19:27:52Z` |
| 258 | Restore App (list) | Read-only listing; the plan's credentials were removed at the end |
| 259 | Restore App (`overwrite=true`) | Restored over the live instance; credentials removed; app back to one replica and serving |
| 260 | Lab Status | **Found the one defect below** |
| 262 | Lab Status | Fix verified live |
| 263 | Remove App (`delete_data=false`) | Two volumes retained by name |
| 264–266 | Reclaim Volume | Report, then delete of both volumes |
| 267 | Lab Status | Clean: nodes Ready, no namespaces, no CronJobs, no retained volumes |

### The defect: a hand-run backup reported as never run

Execution 260 printed `mixpost-backup  0d 0h ago  never run` — two opposite statements in
one row, in the table whose whole purpose is telling an operator whether backups still
work.

`Backup App` runs `kubectl create job --from=cronjob`, which owns the Job to the CronJob.
The controller therefore advances `lastSuccessfulTime` and never touches
`lastScheduleTime`. Both the never-run test and the staleness comparison read
`lastScheduleTime` alone. The visible symptom was the milder half: with the never-run
branch not matching, the staleness test would have string-compared a real RFC 3339
timestamp against the literal `<none>`, found `'2' < '<'`, and alarmed about the backup the
operator had just watched succeed.

Commit `35f3694` makes never-run require both timestamps absent and guards staleness on a
schedule having fired at all. Execution 262 then read `0d 0h ago  ok`.

This is the same shape as every defect this slice found: rendering, lint and syntax-check
passed the file, and only a run against a real CronJob's status could see it.
