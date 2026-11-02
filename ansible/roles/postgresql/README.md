# PostgreSQL

## Native recovery

PostgreSQL native recovery uses `pg_basebackup` over the guest's local socket as the
`postgres` superuser. PBS stores the full PostgreSQL 16 cluster, including databases,
roles and grants, as `postgresql.pxar`; the backup manifest is verified before upload and
restore. Restore validates the target major version, swaps the cluster data directory
while its service is stopped, then checks readiness and local-socket superuser access.
If verification fails, the original data directory is put back. Recovery currently
requires the standard `pg_default` and `pg_global` tablespaces.
