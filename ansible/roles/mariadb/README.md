# MariaDB

## Native recovery

MariaDB native recovery runs `mariadb-dump` over the guest's local root socket with a global
read lock, capturing all persistent databases, routines, events, triggers and grant tables
at one consistent point. PBS stores the logical export and a version/checksum manifest as
`mariadb.pxar` in `host/<instance>`. Restore verifies the export and compatible major/minor
version before stopping MariaDB, moves the original data directory aside on the same
filesystem, initializes a clean system database, and imports the selected point. It puts
the original directory back if initialization, import or health verification fails.

## Docker

`hosting.method: docker` runs the official image under Compose on the stack host. The
container's `/run/mysqld` is the host's `/run/mysqld`, so the host's MariaDB client and the
shared database tasks and `server-recovery.py` reach the server exactly as they reach a
native guest. Its root account has no password and is reachable only over that socket.
Backup is the same export; restore stops the container, moves `/opt/<instance>/data` aside,
lets the image initialize a fresh directory and loads the point, rolling back on failure.

The socket directory is shared per host, so Deploy refuses a second Docker MariaDB backend
on the same stack host; give each backend its own stack.

## Kubernetes

`hosting.method: kubernetes` runs the image as one Deployment, its data on a
default-StorageClass volume and its tuning in a ConfigMap. The Service is a MetalLB
LoadBalancer on the ingress address at `app.port` (shared with Traefik, not routed through
ingress); Deploy refuses a port another Service already holds there. The registry entry
carries `kubernetes`, so the shared database tasks create, version-check and drop an
application's database by SQL run in the server pod (`tasks/database/backend-sql.yml`)
instead of over a guest's socket. Deploy opens the port for each registered consumer
through the firewall.

Its recovery point is the whole server, as on the guests: PBS `database.pxar` holds every
schema and the grants, taken under the global read lock, plus the tuning ConfigMap. The
shared Job reaches root over the pod network (`kubernetes.root_host`), and a restore sets
root's password back to the target's own after loading the point's grant tables. Restore
does not stop the server; stop its consumers first. Remove with `delete_data=true` forgets
the registry entry with the namespace.
