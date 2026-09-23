# Odoo Community recovery

This is the product-specific contract for [Odoo observation issue #244](https://github.com/hardKOrr/homelab-infra/issues/244).
The repository fixture and role checks below are closure evidence for the implementation issue. No live
Odoo, PostgreSQL, PBS, route, credential, or target identity is claimed here.

## Disposition

| Method | Disposition | Recovery unit |
|---|---|---|
| \`native\` | **supported** through \`Backup App\`/\`Restore App\` | The named PostgreSQL database and this Odoo instance's data directory/filestore in one PBS snapshot |
| \`pbs_guest\` | **supported only as a whole-guest fallback** through \`Backup Guest\`/\`Restore Guest\` | The shared \`services\` Docker LXC and every workload on it; it is not an Odoo-only restore |
| \`project_managed\` | **unsupported / not declared** | A redeploy can recreate the Odoo container and configuration, but cannot recreate CRM records, users, or binary attachments |
| rebuild-only | **not sufficient** | Rebuild is a prerequisite for a new target, never a substitute for the paired database and filestore artifact |

Odoo declares only \`native\` in \`ansible/vars/app-defaults/odoo.yml\`. The shared
\`pbs_guest\` method remains available from the guest recovery job, but is intentionally
not declared as an application-only method. Project-managed and rebuild-only paths are
not represented as healthy recovery methods.

The product's documented backup boundary is a database dump paired with the filestore:
the [Odoo 18 on-premise documentation](https://www.odoo.com/documentation/18.0/developer/howtos/website_themes/setup.html)
describes downloading a database backup with filestore and moving the database's filestore
alongside the SQL dump. The [official Odoo image](https://hub.docker.com/_/odoo/) requires
PostgreSQL and says the persistent \`/var/lib/odoo\` data directory must be retained. This
role uses PostgreSQL's native \`pg_dump\`/\`pg_restore\` and PBS's native \`database.pxar\`
and \`filestore.pxar\` members in one snapshot; it does not invent a second Odoo archive
format or call the database manager while \`list_db = False\`.

## Product identity and exact recovery unit

The tracked product identity is Odoo Community \`docker.io/odoo:18.0\`, with the Community
\`crm\` module and PostgreSQL 16 backend \`postgresql-odoo\` by default. The image tag and
database major are declarations, not live evidence of what is installed. Before any
authorized live run, record the installed Odoo version/image digest and PostgreSQL major;
a mismatch is an incompatible target and must remain inactive until the target is rebuilt
with a compatible version.

For instance \`<instance>\`, the native recovery unit is exactly:

1. database \`app.database.name\` (default \`odoo\`) on the registered PostgreSQL backend
   \`app.database.instance\` (default \`postgresql-odoo\`), dumped with \`pg_dump
   --format=custom\`; and
2. the complete host tree at \`app.filestore_path\` (default
   \`/opt/odoo/<instance>/filestore\`), mounted by Compose as Odoo's \`/var/lib/odoo\`
   data directory. This includes the database-specific \`filestore/<database>\` tree,
   attachments and binary fields, plus Odoo data-dir state needed by the instance.

Both are uploaded to the same native PBS snapshot under \`host/<backup_id>\`, with the
members \`database.pxar\` and \`filestore.pxar\`. A point is not usable if either member
or the named dump is absent, unreadable, corrupt, or from an incompatible Odoo/PostgreSQL
pair. The generated \`.crm-modules\` marker and \`odoo.conf\` are reconstructed from the
target's versioned defaults, authored instance config and Vaultwarden values; they are not
a replacement for the database or filestore.

The database guest and its other named databases are dependencies, not Odoo-owned removal
targets. Removing Odoo may remove only its named database/role through the existing database
contract; it must never remove the shared PostgreSQL guest, registry entry, or another
application's database.

## Independent state and dependencies

| State or dependency | Disposition |
|---|---|
| CRM records, stages, activities, users, module state and attachment metadata | In the named PostgreSQL dump |
| Attachments, reports and binary data | In the paired Odoo data-dir/filestore tree |
| Odoo image and Community module set | Rebuild input from tracked defaults and authored config; verify installed version live |
| Database host, port, role and password | Required independently from the registered PostgreSQL backend and the target's Vaultwarden item; never printed or archived |
| Odoo database-manager master password | Required from the target Vaultwarden item for configuration/database administration; never printed or archived |
| Existing Odoo user credentials | User password hashes restore in the database; an authorized operator must independently have the source credential needed for application verification |
| PBS repository, fingerprint, token, encryption/decryption key and target datastore | Required external recovery material; missing or unreadable material is failure |
| SMTP/mail | Resolved from the platform mail contract and independently verified before relying on outbound mail |
| Caddy, Authentik, DNS and Uptime Kuma | External route, identity, DNS and monitoring dependencies; not recreated by this artifact |
| Docker guest CPU, memory, storage, network and free target path | Target prerequisites; the shared services guest is recovered separately by \`pbs_guest\` |
| Extra addons, custom paths, external mounts or devices added outside the defaults | Unsupported unless separately inventoried and backed up; the default Odoo deployment declares none |

The backup stops only this Odoo Compose service before dumping the named database and
filestore. It does not stop or remove PostgreSQL or affect other services on the shared
guest. The database dump is application-consistent at the logical-database boundary; the
filestore is read only after Odoo is quiesced. The PBS client verifies the native artifact
while restoring, \`pg_restore --exit-on-error\` verifies the database load, and the role
requires the Odoo health endpoint before marking the restore complete. These checks do not
turn a configured schedule into live evidence.

## Schedule and evidence identity

The tracked recurring schedule is \`0 3 * * *\` with 14-day daily retention. The scheduled
and on-demand paths use the same role-owned archive definition and the PBS group
\`host/<backup_id>\), defaulting to \`host/<instance>\`. A native snapshot identity is the
provider's full \`host/<backup_id>/<timestamp>\` value; its two pxar members must be listed
and readable from PBS.

This checkout has no authorized live source or artifact. Schedule firing, artifact age,
PBS integrity, version, credential readability, database/filestore consistency and restore
success therefore remain **unknown/deferred**, not healthy by declaration. Observation issue #244 must record
the exact source instance, installed versions, provider snapshot identity, observed age,
integrity result, credentials/key material availability, and application verification
without attaching backup contents or secrets.

## Destination protocol

### New isolated instance

1. Author a distinct target instance config before running \`Restore App\`. It must have a
   target-owned filestore path, target route, and a PostgreSQL database name or backend
   distinct from the source's exact backend host/port/database tuple. Reusing
   \`postgresql-odoo\` with database \`odoo\` would overwrite the source and is refused.
2. Run plan mode first with \`destination=new\`, the source \`instance\`, and the selected
   \`recovery_point\`. Confirm the artifact identity and target collision checks.
3. Run with \`overwrite=true\`. The existing Odoo deployment seam provisions the target's
   database/role, and \`recovery_isolated=true\` suppresses reverse-proxy, SSO, DNS,
   monitoring and guest-record wiring. The source is never contacted.
4. The Odoo adapter extracts and validates both PBS members before stopping the target,
   restores the target database and data-dir/filestore, starts only target Odoo and waits
   for \`/web/health\`. A successful restore is not a production cutover.
5. Verify target-owned identity, CRM records, users/login, representative attachments and
   binary/report downloads, database connection, health and target route on an isolated
   address. Reconcile intended target wiring only after those checks; do not publish the
   source route or clean up the source.

A missing target config, route/storage collision, source-owned path, wrong method or
missing PBS/key/storage/database material stops before mutation. A failed extraction or
restore leaves the target Odoo service stopped and the source artifact retained for retry.

### Existing instance

Use the exact managed target and plan mode first. For a destructive run:

1. Capture or independently verify a fresh B point with \`Backup App\`; retain its full PBS
   identity and pass it as \`pre_restore_point\`. It must be different from the selected A
   point.
2. Confirm the target's current Odoo version, database identity, filestore path and
   running state. The shared PostgreSQL guest remains running; only target Odoo is stopped.
3. Run \`Restore App\` with A as \`recovery_point\`, B as \`pre_restore_point\`, and
   \`overwrite=true\`. The target database and filestore are replaced together.
4. Verify the restored A CRM record, user/login, attachment and binary/report content,
   then verify the target's intended route and database connection.

The acceptance transition is **A → B → restore A**. A B-only CRM record and attachment
must disappear after restoring A, while the retained B point remains readable and can
recover the target. If the replacement fails, the target stays inactive/stopped; recover
B from the retained point or correct the failure before retrying A. No source shutdown,
automatic cutover, unrelated database cleanup or source artifact deletion is performed.

### Whole shared-guest fallback

For a failure of the application path where the whole shared services guest must be
recovered, use \`Backup Guest\`/\`Restore Guest\` with the exact owned LXC VMID and native
\`backup/ct/<vmid>/vzdump-lxc-...\` artifact. This restores Odoo only as part of the shared
guest and affects every services-stack workload. New guest restores remain isolated and
stopped; existing guest replacement requires an independent pre-restore point. Bind
mounts, external filesystems, unowned guests and application-specific database/filestore
consistency remain separate checks. Do not use this fallback to claim an Odoo-only restore.

## Failure and retry matrix

| Case | Expected result |
|---|---|
| Wrong source/target instance, route, database, or storage identity | Refuse before mutation; no source access or cleanup |
| Missing/unreadable PBS token, fingerprint, datastore, encryption key or target credential | Refuse or fail visibly; never report healthy |
| Missing \`database.pxar\`, \`filestore.pxar\`, \`odoo.dump\`, or partial/corrupt snapshot | Refuse before replacement; leave target usable where no mutation began |
| Incompatible Odoo image or PostgreSQL major/schema | Refuse or fail health/database verification; leave target stopped for rebuild/retry |
| Existing target without an independent B point | Refuse before stopping/replacing it |
| Interrupted restore after database/filestore replacement | Leave Odoo stopped; recover B from its retained point, then retry A |
| Missing SMTP, Caddy/SSO/DNS/monitoring or other external dependency | Recovery is incomplete even if Odoo starts; record the exclusion and do not call it healthy |
| Shared services guest restore | Report every affected services-stack workload; never describe it as Odoo-only |

The synthetic fixture test exercises both destinations, source isolation, target-specific
connections, A → B → restore A, retained-B retry, corrupt/incomplete/wrong-target,
identity/storage, missing-key and external-data cases. The focused contract test also
asserts that the target is not wired while isolated and that Odoo's PostgreSQL-plus-
filestore boundary is represented without secrets or backup contents.
