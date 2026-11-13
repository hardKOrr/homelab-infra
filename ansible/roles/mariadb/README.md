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
