# redis

Redis cache backend, run natively on its own guest unless the config selects Docker.

## Docker

`hosting.method: docker` runs the official image (`app.image`) under Compose on a stack
host. `/opt/<instance>` holds the Compose file, `redis.conf` and `data/` (RDB and the ACL
file, owned by the image's uid 999). The host keeps only `redis-tools`; backup, restore and
the readiness check use `redis-cli` against the published `app.port`. Docker recovery
validates staged RDB files in the running Redis image, so a host tool version cannot
reject a newer RDB format. The validator mounts only that file read-only and has no network.
The recovery point is the same RDB plus ACL file as on the native method, and restore swaps
`/opt/<instance>/data`. Without `app.memory.maxmemory` the server gets 256 MiB, because the
stack host is shared.
