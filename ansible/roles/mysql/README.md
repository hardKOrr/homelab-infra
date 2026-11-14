# mysql

Oracle MySQL 8.4 backend for the shared database tasks. Native on its own guest by default.

With `hosting.method: docker` the official image runs under Compose on a stack host, and the
host's `/run/mysqld` is the container's `/var/run/mysqld`. Root has no password and answers
only on that socket. Backup and restore are the usual logical export and reload; the PBS
client runs in Docker. One Docker database backend per stack host, since the socket is shared.

## Kubernetes

`hosting.method: kubernetes` deploys the image as a Deployment in the instance's namespace:
tuning in a ConfigMap, data on a default-StorageClass volume, and a MetalLB LoadBalancer
Service on the ingress address at `app.port`. Deploy refuses a port that another Service
already holds on that address, and opens the port for each registered consumer. Shared
database tasks reach the server by running SQL in its pod (`tasks/database/backend-sql.yml`),
as `roles/mariadb` does; see its README for the recovery point, which is identical in shape:
`database.pxar` holds `mysql.sql` (every schema and the grant tables) beside the tuning
ConfigMap, and restore loads it into the running server, then sets root's password back to
the target's own.
