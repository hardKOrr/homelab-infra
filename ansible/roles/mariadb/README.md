# MariaDB

## Native recovery

MariaDB native recovery runs `mariadb-dump` over the guest's local root socket with a global
read lock, capturing all persistent databases, routines, events, triggers and grant tables
at one consistent point. PBS stores the logical export and a version/checksum manifest as
`mariadb.pxar` in `host/<instance>`. Restore verifies the export and compatible major/minor
version before stopping MariaDB, moves the original data directory aside on the same
filesystem, initializes a clean system database, and imports the selected point. It puts
the original directory back if initialization, import or health verification fails.
