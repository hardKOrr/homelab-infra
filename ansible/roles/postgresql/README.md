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

## Kubernetes

`hosting.method: kubernetes` deploys the official image plus pgvector (`kubernetes.image`)
as a Deployment in the instance's namespace, with its access list in a ConfigMap, its
data on a volume of the cluster's default StorageClass, and a MetalLB Service of type
LoadBalancer answering on the ingress address at `app.port`. Deploy refuses a port already taken on that address by another Service, and
opens the port for every registered consumer. The `postgres` superuser has a generated
password in the instance's Vaultwarden item and may connect only from the pod network
(`kubernetes.superuser_cidr`); `client_cidrs` govern everyone else. Shared database tasks
reach the server by running `psql` in its pod over the local socket
(`tasks/database/backend-psql.yml`).

The recovery point is logical, not a base backup: `database.pxar` holds `postgresql.sql`
(one `pg_dumpall` script: every database, role and grant) beside the access list. Each
database is consistent; the cluster as a whole is not one snapshot. Restore drops every
database and role the point lacks, loads it into the running server and keeps the target's
own superuser password.
