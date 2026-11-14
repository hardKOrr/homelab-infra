# redis

Standalone Redis backend for the shared database tasks. Native on its own guest by default.

## Docker

`hosting.method: docker` runs the official image (`app.image`) under Compose on a stack
host. `/opt/<instance>` holds the Compose file, `redis.conf` and `data/` (RDB and the ACL
file, owned by the image's uid 999). The host keeps only `redis-tools`; backup, restore and
the readiness check run `redis-cli` and `redis-check-rdb` against the published `app.port`.
The recovery point is the same RDB plus ACL file as on the native method, and restore swaps
`/opt/<instance>/data`. Without `app.memory.maxmemory` the server gets 256 MiB, because the
stack host is shared.
