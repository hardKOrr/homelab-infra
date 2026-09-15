# Plane recovery

This is the product-specific contract for [recovery acceptance #80](https://github.com/hardKOrr/homelab-infra/issues/80).
The repository adapter and fixture checks are closure evidence for this slice. They do not
claim a live Plane restore, a live backup schedule, or production cutover.

## Method decision

| Method | Disposition | Recovery unit |
|---|---|---|
| `native` | **supported** through `Backup App`/`Restore App` | The named PostgreSQL database, named Redis dataset, complete Plane server/worker Compose data tree and the local MinIO object-storage path in one PBS snapshot |
| `pbs_guest` | **supported only as a shared whole-guest fallback** through `Backup Guest`/`Restore Guest` | The shared `services` Docker LXC and every workload on it; it is not a Plane-only restore and does not include the separately managed PostgreSQL or Redis guests |
| `project_managed` | **unsupported / not declared** | The repository's native adapter is the application-consistent path; a redeploy alone cannot recreate Plane records, Redis state, RabbitMQ state or uploaded objects |
| rebuild-only | **not sufficient** | A new Compose deployment is a prerequisite for an isolated target, never a substitute for the native paired point |

Plane declares only `native` in `ansible/vars/app-defaults/plane.yml`. The generic
`pbs_guest` method remains available for the shared `services` guest, but is intentionally
not represented as an application-only Plane method. There is no project-managed archive
or rebuild-only claim.

Plane's [official Community backup and restore documentation](https://developers.plane.so/self-hosting/manage/backup-restore)
identifies PostgreSQL, Redis and uploads as the durable backup boundary. The role in this
repository is a custom Compose deployment rather than one installed by Plane's `setup.sh`
or Prime CLI, so that command is not invoked. The native adapter preserves the same product
data boundary with PostgreSQL's `pg_dump`/`pg_restore`, Redis's native RDB format and PBS's
native `pxar` members. It adds the repository-owned RabbitMQ/server-worker data tree and
local MinIO object-storage tree because those paths are part of this deployment's declared
recovery unit; it does not invent a second product archive format.

## Product identity and exact recovery unit

The tracked product identity is Plane Community's pinned service release `v1.4.2` in
`ansible/vars/app-defaults/plane.yml`, with PostgreSQL major `16`, the named backend
`postgresql-plane`, database `plane`, Redis backend `redis-plane`, and local object storage
`plane-minio`. These are declarations, not live evidence. Before an authorized live run,
record the installed Plane image tags/digests, PostgreSQL major, Redis version and the
Compose service image versions. A mismatch is an incompatible target and must remain
inactive until rebuilt with a compatible set.

For instance `<instance>`, the native recovery unit is exactly:

1. PostgreSQL database `app.database.name` (default `plane`) on registered backend
   `app.database.instance` (default `postgresql-plane`), captured as a custom-format
   `pg_dump`;
2. the complete named Redis dataset for `app.redis.instance` (default `redis-plane`),
   captured as `dump.rdb` with the authenticated Redis client;
3. the complete host tree at `app.data_path` (default `/opt/<instance>/data`), including
   the Plane server/worker API, worker, beat and migrator media paths and the local
   `plane-mq` RabbitMQ data directory; and
4. the complete host tree at `app.object_storage_path` (default
   `/opt/<instance>/object-storage`), mounted by `plane-minio` as its object-storage
   volume and containing the declared `plane` bucket.

All four members are uploaded to one native PBS snapshot under `host/<backup_id>`:
`database.pxar` contains `plane.dump` plus the non-secret `plane-release.txt` compatibility
marker, `redis.pxar` contains `dump.rdb`, `data.pxar` contains the server/worker and
RabbitMQ tree, and `object-storage.pxar` contains the local MinIO tree. Restore compares the
marker with the target's declared release before mutation. A point is unusable if any member
is absent, unreadable, corrupt, or from an incompatible Plane/PostgreSQL/Redis set. PBS's
repository identity, snapshot index, retention and server-side integrity remain independent
evidence; a configured schedule is not proof that this point exists.

Plane's application `SECRET_KEY`, live-server key, RabbitMQ password and MinIO access
keys are hidden fields in `homelab-infra/apps/<instance>` in Vaultwarden. They are not
placed in the PBS artifact or evidence. A new target receives the source Plane keys before
its environment is rendered so the restored database, RabbitMQ state and MinIO data remain
usable. The target's database and Redis connection credentials are independently resolved
from the target's named backends and Vaultwarden items.

The PostgreSQL and Redis guests are named dependencies, not Plane-owned removal targets.
Removing Plane may remove only its named database/role under the existing database
contract; it must never remove either shared backend, another database, another Redis
dataset, or the `plane-minio` data belonging to a different instance.

## Independent state and prerequisites

| State or dependency | Disposition |
|---|---|
| Plane workspaces, projects, issues, users, settings and application metadata | Named PostgreSQL `plane.dump` |
| Redis-backed Plane state and queues | Named `redis-plane` RDB; the backend must be dedicated to this recovery unit or its other tenants are in the affected scope |
| Plane API/worker/beat media and local RabbitMQ durable state | Paired `data.pxar` |
| Uploaded assets and object metadata stored by local MinIO | Paired `object-storage.pxar` |
| Plane application keys and RabbitMQ/MinIO credentials | Required independently from the source Vaultwarden item; carried to a new target, never archived as evidence |
| Target database/Redis connection credentials | Required independently from target Vaultwarden items; missing or unreadable credentials fail recovery |
| Plane images, Compose definitions and PostgreSQL/Redis package majors | Rebuild inputs from tracked defaults and the target's authored config; verify installed versions live |
| PBS repository, datastore, fingerprint, API token and any encryption/decryption material | Required external recovery material; missing or unreadable material is failure |
| Docker guest CPU, memory, storage, network, free port/address and target path | Target prerequisites; a new target must be distinct and isolated |
| Caddy, Authentik, DNS, Uptime Kuma, mail, webhooks and external integrations | External route, identity, delivery and integration dependencies; not recreated by this point |
| Hardware passthrough or external filesystem mounts not declared by Plane defaults | Unsupported unless separately inventoried and backed up; no dedicated Plane device is declared |

The backup stops the complete Plane Compose project before dumping PostgreSQL and Redis or
reading the two owned paths. It does not stop either backend guest or unrelated services on
the shared `services` guest. Scheduling adds a cron package/service and one root cron entry
on that shared guest; no other workload is modified by the schedule itself. PostgreSQL dump, Redis RDB validation and all four PBS members
must succeed before the point is marked complete. The project is started again after a
backup attempt; if that restart fails, the operation is failed and the operator must not
call the point healthy.

The restore extracts and validates all four PBS members before stopping the target. It
checks the PostgreSQL dump with `pg_restore --list` and the Redis RDB with
`redis-check-rdb`, replaces the target's named database and Redis data, restores both
owned trees, renders the carried source keys, starts the complete target Compose project
and requires its HTTP health endpoint. A failure leaves Plane stopped for inspection and
retry; it does not delete the source, selected point or retained B point.

## Schedule and evidence identity

The tracked recurring schedule is `0 3 * * *` in the shared services guest's local
timezone. Deployment installs cron and a root-only recovery helper/config only when backup
is enabled and PBS is available; Backup App invokes that exact same helper. New restore
targets do not receive an active timer until the selected point passes verification and the
normal deploy/wiring pass occurs. Both paths use PBS group `host/<backup_id>`, defaulting
to `host/<instance>`, and upload all four members in one point. The helper does not prune
or delete existing snapshots. Any datastore-wide PBS retention policy is independent and
its identity/age must be verified during #80. A native point identity is the provider's
full `host/<instance>/<timestamp>` value, with all four members listed and readable from PBS.

This checkout has no authorized live source, installed-version observation or artifact.
Schedule firing, artifact age, PBS integrity, PostgreSQL/Redis readability, key availability,
external dependencies, consistency and restore success therefore remain **unknown/deferred**,
not healthy by declaration. #80 must record the exact source instance and Proxmox target,
installed versions, full provider artifact identity, age, integrity result, member list,
credential/key checks, external dependency checks, both destination results and application
assertions without attaching backup contents or secrets.

## Destination protocol

### New isolated instance

1. Author a distinct target config before `Restore App`. The target must use target-owned
   data and object-storage paths, a distinct route identity, and PostgreSQL and Redis
   backends whose host/port/data identity is different from the source. Reusing
   `postgresql-plane`/`plane` or `redis-plane` for a new target is refused because it
   could overwrite the source.
2. Run plan mode first with `destination=new`, source `instance`, target and selected
   `recovery_point`. Confirm the native PBS identity and target collision checks. Plan mode
   does not provision or stop anything.
3. Run with `overwrite=true`. The existing Plane deployment seam provisions the target
   Compose project, target database and target paths with `recovery_isolated=true`; its
   wiring play ends before reverse-proxy, SSO, DNS, monitor or guest-record publication.
4. The Plane adapter validates `database.pxar`, `redis.pxar`, `data.pxar` and
   `object-storage.pxar`, restores them to the target's named backends and paths, carries
   the source application keys, and starts only the target recovery unit. The source is
   never contacted or changed.
5. Verify target-owned Plane identity and route, workspace/project/issue records, uploaded
   asset retrieval, server/worker/beat health, RabbitMQ state, Redis-backed behavior,
   database connectivity, source-key decryptability and the absence of production wiring.
   Reconcile intended connections only after those checks; this is not a production
   cutover.

A missing target config, route/storage collision, source-owned target backend, missing PBS
material, missing key, missing artifact member, corrupt archive or incompatible version
stops before replacement. A failed extraction or restore leaves the target inactive/stopped
and retains the selected point for retry.

### Existing instance

Use the exact managed target and plan mode first. For a destructive run:

1. Run `Backup App` for the current target and independently verify a fresh B point. Retain
   its full PBS identity and pass it as `pre_restore_point`; B must be different from A.
   Because Redis is part of the native point, this preserves the target's database, Redis,
   server/worker and object-storage state together.
2. Confirm the exact target's current Plane version, backend identities, paths, key
   availability and running state. Do not use the source A point as the B point.
3. Run `Restore App` with A as `recovery_point`, B as `pre_restore_point`,
   `destination=existing` and `overwrite=true`. The target project is quiesced before its
   four state members are replaced; no shared backend guest is removed.
4. Verify A's representative records, assets, worker/queue state, Redis behavior, keys,
   credentials and target route. The acceptance transition is **A → B → restore A**:
   add a B-only record/object/queue marker, capture B, restore A, and prove B-only state is
   absent while the retained B point remains usable.

If replacement is interrupted, the target remains stopped/inactive and incomplete. Recover
B from the independently retained point, verify it, then retry A. No source shutdown,
implicit cutover, unrelated-resource cleanup or source-artifact deletion is performed.

### Shared whole-guest fallback

When the application path cannot be used and the shared Docker guest itself must be
recovered, use `Backup Guest`/`Restore Guest` with the exact owned `services` LXC VMID and
native `backup/ct/<vmid>/vzdump-lxc-...` artifact. This recovers Plane only as part of the
shared guest and affects every workload on that guest. The separately managed PostgreSQL
and Redis guests, their application data, MinIO object storage, credentials and application
consistency still require their own evidence. A new guest target remains isolated/stopped;
an existing guest replacement requires an independent pre-restore point. Never describe
this fallback as a Plane-only restore.

## Failure and retry matrix

| Case | Expected result |
|---|---|
| Wrong source/target instance, database, Redis backend, route or storage identity | Refuse before mutation; no source access or cleanup |
| Missing/unreadable PBS token, fingerprint, datastore, encryption/decryption key or target credential | Refuse or fail visibly; never report healthy |
| Missing `database.pxar`, `redis.pxar`, `data.pxar`, `object-storage.pxar`, `plane.dump`, `plane-release.txt` or `dump.rdb` | Refuse before replacement; leave target usable where no mutation began |
| Corrupt PBS member, invalid PostgreSQL dump or invalid Redis RDB | Refuse before replacement; retain the selected point for investigation |
| Incompatible Plane image, PostgreSQL major, Redis version or schema | Refuse or fail health/database verification; leave target stopped for rebuild/retry |
| Existing target without an independent B point | Refuse before stopping/replacing it |
| Backup/restore already running for this Plane instance or a stale lock remains | Refuse overlapping work; inspect the owner before clearing a stale lock and retrying |
| Interrupted replacement after database/path/Redis changes | Leave Plane stopped; recover B from its retained point, then retry A |
| Missing source application key or mismatch with restored data | Refuse or leave target stopped; restore the verified key and retry |
| Missing MinIO, RabbitMQ, Caddy, SSO, DNS, mail or external integration material | Recovery is incomplete even if Plane starts; record the exclusion and do not call it healthy |
| Shared `services` guest restore | Report every affected stack workload; never describe it as Plane-only |

The synthetic fixture test exercises both native destinations, source isolation, target
identity and connections, A → B → restore A, retained-B retry, wrong target, incomplete or
corrupt artifact, incompatible version, missing key, excluded external data, storage and
shared-scope boundaries. The focused contract test checks all four native artifact members,
source-key handling, target Redis separation, isolated wiring and stopped-on-failure behavior.

Repository/fixture evidence is the closure scope for this issue. Live Plane validation is
deferred to the existing [recovery acceptance roll-up #80](https://github.com/hardKOrr/homelab-infra/issues/80)
with an authorized isolated source/destination. It must record the installed Plane version,
native PBS artifact identity, recurring schedule observation, all four member/integrity
results, both destination results, independent PostgreSQL/Redis/key/object-storage checks,
application assertions and final target state. No production cutover, source shutdown or
cleanup is implied.
