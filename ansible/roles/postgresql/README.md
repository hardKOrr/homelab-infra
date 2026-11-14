# PostgreSQL

## Native recovery

PostgreSQL native recovery uses `pg_basebackup` over the guest's local socket as the
`postgres` superuser. PBS stores the full PostgreSQL 16 cluster, including databases,
roles and grants, as `postgresql.pxar`; the backup manifest is verified before upload and
restore. Restore validates the target major version, swaps the cluster data directory
while its service is stopped, then checks readiness and local-socket superuser access.
If verification fails, the original data directory is put back. Recovery currently
requires the standard `pg_default` and `pg_global` tablespaces.

## Docker

`hosting.method: docker` builds on the official `postgres:16` image with pgvector under
Compose on a stack host. The host's `/var/run/postgresql` is mounted at `/run/pgsock`
in the container: access there is trust, limited by the
directory's mode, so the shared database tasks and the host's PostgreSQL client reach the
server as the host `postgres` user. `pg_hba.conf` sits in `/opt/<instance>` and is passed
with `hba_file`, so `client_cidrs` govern the published port. The recovery point is the same
base backup; restore swaps `/opt/<instance>/data`. Host packages include the server tools
because `pg_verifybackup` requires `pg_waldump`; automatic native cluster creation is
disabled. A stack host takes one such backend.
