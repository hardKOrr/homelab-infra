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

### Close plan

1. Decide topology, failure domains, ingress address, external access classes and secret
   ownership.
2. Provision a fresh cluster and prove an idempotent cluster re-run.
3. Add the Kubernetes application adapter behind the existing app/job contract.
4. Deploy and wire one Foxglove pilot through the existing estate.
5. Prove removal, restore and declared node-failure behavior.
6. Use the proven adapter for suitable entries in `408-app-catalog` when that slice lands
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
