# mysql

Standalone Oracle MySQL 8.4 backend that applications reach through the shared database
tasks. It runs natively on its own LXC or VM by default.

## Docker

`hosting.method: docker` runs the official image under Compose on the stack host. The
container's `/var/run/mysqld` is the host's `/run/mysqld`, so the host's MySQL client and the
shared database tasks and `server-recovery.py` reach the server exactly as they reach a
native guest. Its root account has no password and is reachable only over that socket.
Backup and restore are the same logical export and reload, with the PBS client in Docker.

The socket directory is shared per host, so Deploy refuses a second Docker database backend
on the same stack host; give each backend its own stack.
