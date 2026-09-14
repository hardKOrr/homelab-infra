# n8n recovery

This is the product-specific contract for [recovery acceptance #80](https://github.com/hardKOrr/homelab-infra/issues/80) and
[n8n recovery issue #182](https://github.com/hardKOrr/homelab-infra/issues/182). The repository fixture and role checks are
closure evidence for this slice. No live n8n, PostgreSQL, PBS, route, credential, or target
identity is claimed here; live acceptance is deferred to #80 while the lab is unavailable.

## Disposition

| Method | Disposition | Recovery unit |
|---|---|---|
| `native` | **supported** through `Backup App`/`Restore App` | The named PostgreSQL database and this n8n instance's `/home/node/.n8n` data tree in one PBS snapshot, with the Vaultwarden encryption key carried independently |
| `pbs_guest` | **supported only as a whole-guest fallback** through `Backup Guest`/`Restore Guest` | The shared `services` Docker LXC and every workload on it; it is not an n8n-only restore |
| `project_managed` | **unsupported / not declared** | A redeploy recreates the container and configuration but does not independently restore the named database, encrypted credentials, or instance data |
| rebuild-only | **not sufficient** | Rebuild is a prerequisite for a new target, never a substitute for the database, data tree, and encryption key |

n8n declares only `native` in `ansible/vars/app-defaults/n8n.yml`. The shared
`pbs_guest` method remains available from the guest recovery job, but is intentionally not
declared as an application-only method. Project-managed and rebuild-only paths are not
represented as healthy application methods.

n8n's documented self-hosted deployment uses PostgreSQL for credentials, past executions
and workflows, and recommends retaining the `.n8n` directory even with PostgreSQL because
it contains other important instance data. Its documented `N8N_ENCRYPTION_KEY` protects
credentials in the database; losing it makes those credentials unreadable. The native
adapter therefore uses a quiesced PostgreSQL logical dump plus a read-only PBS `pxar` of
the mounted data tree. It does not create an undocumented n8n archive format or use the
CLI's decrypted credential export. The [n8n deployment environment-variable documentation](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/deployment/)
and [n8n Server CLI documentation](https://docs.n8n.io/llms-full.txt) are the upstream
references for the key and the product's backup/migration tooling.

## Product identity and exact recovery unit

The tracked product identity is `docker.n8n.io/n8nio/n8n:1.104.2` with the named PostgreSQL
16 backend `postgresql-n8n`, database `n8n` and role `n8n` by default. The image tag and
PostgreSQL major are declarations, not live evidence of what is installed. Before an
authorized live run, record the installed n8n image version/digest and PostgreSQL major;
a mismatch is an incompatible target and must remain inactive until rebuilt with a
compatible pair.

For instance `<instance>`, the native recovery unit is exactly:

1. database `app.database.name` (default `n8n`) on registered backend
   `app.database.instance` (default `postgresql-n8n`), dumped with PostgreSQL's custom
   `pg_dump` format;
2. the complete host tree at `app.data_path` (default `/opt/<instance>/data`), mounted by
   Compose as n8n's `/home/node/.n8n` directory; and
3. the encryption key held in hidden Vaultwarden field `encryption_key` in
   `homelab-infra/apps/<instance>`.

The database and data tree are uploaded to the same native PBS snapshot under
`host/<backup_id>` as `database.pxar` (containing `n8n.dump`) and `data.pxar`. The key is
not printed or placed in the artifact; it is required independently and is copied to a
new target's canonical Vaultwarden item before the target starts. A point is unusable if
either member is absent, unreadable, corrupt, or from an incompatible n8n/PostgreSQL pair,
or if the source key cannot be read.

The n8n database's workflow rows, credential rows, users, projects, permissions, tags and
execution metadata belong to the named database. Encrypted credential values remain
unreadable without the key. The `.n8n` tree carries instance-local assets and settings that
n8n recommends retaining with PostgreSQL; it is not a substitute for the database. External
execution/provider credentials, provider availability, mail, webhook consumers and any
external execution or binary-data store remain separate dependencies and are never claimed
healthy by the snapshot alone.

The PostgreSQL guest and its other named databases are dependencies, not n8n-owned removal
targets. Removing n8n may remove only its named database/role through the existing database
contract; it must never remove the shared PostgreSQL guest, registry entry, or another
application's database.

## Independent state and dependencies

| State or dependency | Disposition |
|---|---|
| Workflows, workflow versions, users, projects, permissions, tags and execution metadata | In the named PostgreSQL dump |
| Encrypted n8n credential records | In the named PostgreSQL dump; usable only when the source encryption key is available |
| n8n encryption key | Required independently from the source Vaultwarden item and carried to a new target; never printed or stored in the backup artifact |
| `.n8n` instance data, settings and source-control assets | In the paired `data.pxar` tree |
| n8n image, node packages and PostgreSQL major | Rebuild input from tracked defaults and authored config; verify installed versions live |
| Database host, port, role and password | Required independently from the registered PostgreSQL backend and target Vaultwarden item; never printed or archived |
| PBS repository, fingerprint, token, encryption/decryption key and target datastore | Required external recovery material; missing or unreadable material is failure |
| Provider/execution credentials and upstream services | Separate external dependencies; verify each independently before exercising workflows |
| SMTP, webhook consumers, DNS, Caddy, Authentik and Uptime Kuma | External route, identity, delivery and monitoring dependencies; not recreated by this artifact |
| Docker guest CPU, memory, storage, network and free target path | Target prerequisites; the shared services guest is recovered separately by `pbs_guest` |
| External binary-data storage, mounts, custom nodes and devices outside defaults | Unsupported unless separately inventoried and backed up |

The backup stops only this n8n Compose service before dumping its named database and reading
the data tree. It does not stop PostgreSQL or affect other services on the shared guest. The
dump is application-consistent at the logical database boundary, the data tree is read only
after n8n is quiesced, PBS verifies the native members while restoring, `pg_restore
--exit-on-error` verifies the database load, and the role requires `/healthz` before marking
the restore complete. These checks do not turn a configured schedule into live evidence.

## Schedule and evidence identity

The tracked recurring schedule is `0 3 * * *` with 14-day daily retention. Scheduled and
on-demand paths use the same role-owned archive definition and PBS group
`host/<backup_id>`, defaulting to `host/<instance>`. A native snapshot identity is the
provider's full `host/<backup_id>/<timestamp>` value; its `database.pxar` and `data.pxar`
members must both be listed and readable from PBS.

This checkout has no authorized live source or artifact. Schedule firing, artifact age, PBS
integrity, installed version, key/database readability, provider credentials, consistency
and restore success therefore remain **unknown/deferred**, not healthy by declaration. #80
must record the exact source instance, installed versions, provider snapshot identity,
observed age, integrity result, key and credential availability, external dependency checks,
and application verification without attaching backup contents or secrets.

## Destination protocol

### New isolated instance

1. Author a distinct target instance config before running `Restore App`. It must have
   target-owned data storage, route identity, and a PostgreSQL database name or backend
   distinct from the source's exact backend host/port/database tuple. Reusing
   `postgresql-n8n` with database `n8n` would overwrite the source and is refused.
2. Run plan mode first with `destination=new`, the source `instance`, and selected
   `recovery_point`. Confirm the artifact identity and target collision checks.
3. Run with `overwrite=true`. The existing n8n deployment seam provisions the target
   database, role, data path and generated target key; `recovery_isolated=true` suppresses
   reverse-proxy, SSO, DNS, monitoring and guest-record wiring. The source is never
   contacted.
4. The n8n adapter extracts and validates both PBS members before stopping the target,
   replaces the target database and data tree, carries the source encryption key into the
   target Vaultwarden item, starts only target n8n and waits for `/healthz`. This is not a
   production cutover.
5. Verify target-owned identity, workflow state, credential decryptability with the carried
   key, user/login, named database connection, representative workflow execution against
   a safe fixture/provider, health and isolated target route. Reconcile intended target
   wiring only after those checks; do not publish the source route or clean up the source.

A missing target config, route/storage collision, source-owned path, wrong method or missing
PBS/key/storage/database material stops before mutation. A failed extraction or restore
leaves the target n8n service stopped and the source artifact retained for retry.

### Existing instance

Use the exact managed target and plan mode first. For a destructive run:

1. Capture or independently verify a fresh B point with `Backup App`; retain its full PBS
   identity and pass it as `pre_restore_point`. It must be different from selected A.
2. Confirm the target's current n8n image, database identity, data path, key readability
   and running state. The shared PostgreSQL guest remains running; only target n8n stops.
3. Run `Restore App` with A as `recovery_point`, B as `pre_restore_point`, and
   `overwrite=true`. The target database and data tree are replaced together, and the
   target retains the source key when source and target differ.
4. Verify restored A workflows, encrypted credential decryptability, user/login, database
   connection, safe workflow execution and intended target route.

The acceptance transition is **A → B → restore A**. A B-only workflow and credential record
must disappear after restoring A, while retained B remains readable and can recover the
target. If replacement fails, the target stays inactive/stopped; recover B from its retained
point or correct the failure before retrying A. No source shutdown, automatic cutover,
unrelated database cleanup or source artifact deletion is performed.

### Whole shared-guest fallback

For a failure of the application path where the whole shared services guest must be
recovered, use `Backup Guest`/`Restore Guest` with the exact owned LXC VMID and native
`backup/ct/<vmid>/vzdump-lxc-...` artifact. This restores n8n only as part of the shared
guest and affects every services-stack workload. New guest restores remain isolated and
stopped; existing guest replacement requires an independent pre-restore point. Bind mounts,
external filesystems, unowned guests, n8n database/data consistency and provider credentials
remain separate checks. Do not use this fallback to claim an n8n-only restore.

## Failure and retry matrix

| Case | Expected result |
|---|---|
| Wrong source/target instance, route, database, or storage identity | Refuse before mutation; no source access or cleanup |
| Missing/unreadable PBS token, fingerprint, datastore, encryption key or target credential | Refuse or fail visibly; never report healthy |
| Missing `database.pxar`, `data.pxar`, `n8n.dump`, or partial/corrupt snapshot | Refuse before replacement; leave target usable where no mutation began |
| Incompatible n8n image or PostgreSQL major/schema | Refuse or fail health/database verification; leave target stopped for rebuild/retry |
| Existing target without an independent B point | Refuse before stopping/replacing it |
| Interrupted restore after database/data replacement | Leave n8n stopped; recover B from its retained point, then retry A |
| Missing source key or key/database mismatch | Refuse or leave target stopped; carry the verified source key and retry |
| Missing provider/execution credential or upstream service | Recovery is incomplete even if n8n starts; record the exclusion and do not call it healthy |
| Shared services guest restore | Report every affected services-stack workload; never describe it as n8n-only |

The synthetic fixture test exercises both destinations, source isolation, target-specific
connections, A → B → restore A, retained-B retry, corrupt/incomplete/wrong-target,
identity/storage, missing-key and external-data cases. The focused contract test asserts
that the target is not wired while isolated and that n8n's PostgreSQL-plus-data-plus-key
boundary is represented without secrets or backup contents.
