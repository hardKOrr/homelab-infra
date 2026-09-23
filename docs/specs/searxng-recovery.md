# SearXNG recovery

## Method decision

SearXNG's application recovery disposition is **rebuild-only**. Its current implementation is
an internal Kubernetes workload with no application data volume. The shared Kubernetes
cluster's whole-guest PBS path remains available, but it is not an application-only restore.

| Method | Disposition | Recovery unit |
|---|---|---|
| `pbs_guest` | **supported only as a shared whole-guest fallback** through `Backup Guest`/`Restore Guest` | The exact PBS-backed Kubernetes node/cluster guest set and every workload on those guests; never SearXNG alone |
| `native` | **unsupported / not declared** | SearXNG has no product-native export/import operation used by this role; the role's Kubernetes Secret and Deployments are declarative inputs, not a native backup artifact |
| `project_managed` | **unsupported / not declared** | No product-specific restore role task exists. A redeploy is the rebuild-only path and must not be represented as an application archive |
| rebuild-only | **supported** | The rendered SearXNG namespace configuration and a fresh limiter dependency, rebuilt from tracked defaults, target configuration and canonical credentials |

The ownership map records `rebuild_only: true` for SearXNG, while the application defaults
intentionally declare no `recovery.methods` or `backup` block. Consequently the generic
application Backup/Restore action is not generated. This prevents a missing artifact from
being mistaken for successful recovery and keeps unsupported native/project-managed methods
out of the resolver.

This decision applies to the tracked implementation: `searxng/searxng:latest` with
`redis:7-alpine` as the in-namespace limiter deployment. `latest` is a declaration, not the
installed product version. Before live evidence, record the exact image digest/version
actually running on the source and target. The upstream SearXNG documentation describes
`settings.yml` as the user configuration input and documents a Valkey/Redis-backed limiter;
it does not supply an application backup format for this deployment:

- [SearXNG settings](https://docs.searxng.org/admin/settings/settings.html)
- [SearXNG server settings](https://docs.searxng.org/admin/settings/settings_server.html)
- [SearXNG limiter](https://docs.searxng.org/admin/searx.limiter.html)
- [SearXNG container installation](https://docs.searxng.org/admin/installation-docker.html)

## Exact recovery unit and dependencies

The application recovery unit is the desired SearXNG namespace, not a byte-for-byte
application archive:

- the `settings` Secret containing the rendered `settings.yml`;
- the SearXNG Deployment, Service and Ingress, with the target namespace and route identity;
- the limiter Redis Deployment and Service, started empty on rebuild; and
- the target-owned cluster placement and Kubernetes object identity.

The role deliberately creates no PersistentVolumeClaim or durable data volume for either
SearXNG or Redis. Its settings Secret is mounted as ephemeral configuration input. Search
results, engine suspension state and limiter counters are not treated as recoverable product
data. A fresh empty limiter is therefore an intended successful rebuild,
not missing state. If a future implementation mounts `/var/cache/searxng`, adds a PVC, or
moves the limiter to a named external service, this disposition must be revisited before
calling the cache disposable.

| State or dependency | Disposition |
|---|---|
| Instance name, `instance_name`, formats, base URL and other rendered settings | Rebuilt from tracked defaults plus the target's authored `config/apps/<instance>.yml`; target configuration is required for a new isolated instance |
| `secret_key` | Canonical Vaultwarden credential. A new key can start a fresh rebuild but does not preserve existing cookies/CSRF sessions; retaining the existing key is required for continuity |
| Limiter Redis password | Canonical Vaultwarden credential. It authenticates the fresh in-namespace limiter; its counters are disposable and may be regenerated for a rebuild |
| Limiter counters and transient engine state | Intentionally excluded; the target starts with an empty cache |
| SearXNG image and Redis image | Rebuild inputs from tracked defaults; verify exact installed versions/digests live |
| Kubernetes nodes, cluster control plane, StorageClass and ingress VIP | Shared hosting prerequisites. PBS recovery of these guests affects every workload on the cluster |
| Caddy, DNS, Authentik, Uptime Kuma and firewall/access policy | External wiring dependencies; isolated rebuild must not mutate them before verification |
| Search-engine upstream reachability, egress policy, rate limits and engine credentials | External provider behavior; verify independently before using search results as acceptance evidence |
| PBS datastore, repository, fingerprint, token and encryption/decryption material | Required only for the shared `pbs_guest` fallback; no SearXNG application artifact exists |
| External storage, database, hardware and passthrough devices | None are declared by this role. Any future mount or named backend is outside this contract until separately inventoried |

Missing or unreadable Vaultwarden material, target configuration, cluster access or an
external dependency is a failed or incomplete recovery for the capability it supports. A
fresh process responding to `/healthz` alone does not prove credential continuity, intended
route wiring, upstream search behavior or the shared cluster's recovery.

## Schedule and artifact evidence

The SearXNG Deploy job is not a recurring backup schedule and the application has no
application-specific artifact identity. There is no `host/<backup_id>` SearXNG snapshot to
list, age-check, integrity-check or restore. The shared PBS fallback uses the provider's
native identity, `backup/vm/<vmid>/vzdump-qemu-<vmid>-...` for the current Kubernetes VM
implementation, and its schedule, age, integrity, credentials and restore result are
owned by the guest recovery evidence.

The repository cannot read a live image digest, schedule, PBS point, Vaultwarden item,
cluster state or external provider from fixtures. Those values remain **unknown** until observed live
and must never be promoted to healthy by this specification. A live acceptance record must include the
exact source, isolated target, installed versions, any shared PBS artifact identity, age,
integrity and consistency result, credential/key availability, external dependency checks,
and final application assertions without recording secrets or backup contents.

## Destination protocol

### New isolated rebuild target

1. Author a distinct target instance configuration, including a target-specific
   `routing.subdomain`, before running the Deploy SearXNG job. Verify that the namespace,
   route, address and Vaultwarden item do not collide with the source.
2. Run the job in plan/review discipline and select `recovery_isolated=true`. The deploy
   creates only the target namespace resources and ends before reverse-proxy, SSO, DNS,
   monitoring and shared-guest recording tasks. It does not contact or mutate the source.
3. Verify the target's exact image versions, namespace object set, rendered settings,
   target-owned identity, `/healthz`, JSON search format and fresh empty limiter behavior
   through an isolated cluster path. Do not publish the production route or perform a
   cutover as part of this recovery.
4. After the application checks pass, reconcile intended target wiring as a separate,
   deliberate operation. Do not remove the source or clean up the target automatically.

A missing target config, colliding namespace/route/address, unavailable cluster or missing
credential leaves the target untrusted. A failed isolated deployment remains unwired and
must be inspected or retried explicitly; it is not a successful recovery.

### Existing target: A → B → rebuild A

1. Record A's target identity, rendered configuration revision, image versions, key
   continuity expectation and current health. Preserve an independent B point before any
   replacement; for this rebuild-only path that point is the separately retained
   target-owned declarative configuration/health record, not an invented PBS application
   artifact. The shared `pbs_guest` fallback must instead retain an independent native PBS
   B guest point.
2. Change only the disposable target's declared configuration to a distinguishable B
   revision and run the target deploy. Confirm the B-only setting is present and the source
   and unrelated cluster workloads are unchanged.
3. Restore A by putting the independently retained A configuration back in place and
   re-running Deploy SearXNG for the exact existing target with `recovery_isolated=false`
   only when intended wiring is already valid. The role reapplies target-owned objects and
   starts a fresh limiter; it does not copy source data, delete a source namespace or
   silently switch a production route.
4. Verify that A's rendered configuration, credential/key behavior, target identity,
   health and intended connections return, and that the B-only setting is gone. The empty
   limiter is expected and must not be counted as lost durable state.

If reconciliation fails, the target is not accepted as recovered: leave it isolated or in
maintenance, retain the A/B records, record the exact retry action, and correct the failure
before retrying. Do not clean up source resources or discard the independent point.

### Shared whole-guest fallback

For a cluster failure, use `Backup Guest`/`Restore Guest` for the exact project-owned
Kubernetes VM(s). This restores SearXNG only as part of the shared cluster and affects every
workload on the selected guest set. A new guest remains stopped and isolated; existing guest
replacement requires independent pre-restore proof. Cluster nodes, ingress, StorageClass,
other namespaces, external storage, Vaultwarden, Caddy and application credentials remain
separate recovery checks. Never describe this fallback as a SearXNG-only restore.

## Failure and retry matrix

| Case | Expected result |
|---|---|
| `native` or `project_managed` selected | Unsupported capability; no application archive or restore task is inferred |
| New target has a wrong/colliding namespace, route, address, storage or identity | Refuse or keep the target unwired before external wiring; source remains untouched |
| Missing target configuration, cluster access or Vaultwarden key/password | Rebuild fails or remains incomplete; do not report healthy |
| Missing, unreadable, incomplete or corrupt shared PBS artifact | Shared guest restore refuses before mutation; retain the source and recovery point for retry |
| Incompatible image, cluster version or rendered settings | Fail verification; leave the target isolated/inactive and retry after correcting the version/configuration |
| Limiter Redis unavailable or empty after rebuild | Search recovery is incomplete if the limiter is required for the declared policy; empty counters alone are not failure |
| External search engine, DNS, Caddy, SSO, monitoring or egress unavailable | Record the dependency failure; a healthy process is not a healthy integrated recovery |
| Existing restore without an independent B point | Refuse replacement; preserve the target until a separate point/record is available |
| Interrupted existing rebuild after object replacement | Leave target isolated/in maintenance, preserve A/B records, and retry deterministically |
| Shared cluster guest restore | Report all affected workloads; no implicit cutover, source mutation or unrelated cleanup |

The focused fixture test exercises the shared PBS fallback through the common acceptance
protocol for both `new` and `existing`, including source isolation, target identity,
A → B → restore A, independent B retention, missing key, wrong target, storage boundary,
corrupt/incomplete artifact, shared scope, and interrupted replacement retry. It separately
models the rebuild-only new and existing paths and verifies that an empty limiter cache is
not required state.
