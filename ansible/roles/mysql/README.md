# mysql

Oracle MySQL 8.4 backend for the shared database tasks. Native on its own guest by default.

With `hosting.method: docker` the official image runs under Compose on a stack host, and the
host's `/run/mysqld` is the container's `/var/run/mysqld`. Root has no password and answers
only on that socket. Backup and restore are the usual logical export and reload; the PBS
client runs in Docker. One Docker database backend per stack host, since the socket is shared.
