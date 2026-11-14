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

## Kubernetes

`hosting.method: kubernetes` deploys the official image (`app.image`) as a Deployment in
the instance's namespace, with `redis.conf` in a ConfigMap, its data and ACL file on a
volume of the cluster's default StorageClass (`app.storage` Gi), and a MetalLB
LoadBalancer Service on the ingress address, port `app.port`. Deploy refuses a port
another Service holds there and opens it for each registered consumer (`redis_host` and
`redis_port` in the consumer's item). An init container sets the
`default` login to the Vaultwarden password and keeps any other ACL user.

The recovery point is `database.pxar`: `redis.rdb` (taken with `redis-cli --rdb`) beside
`redis.acl`, plus the configuration ConfigMap. Restore starts a throwaway Redis holding the
RDB in the Job and makes the running server replicate it, so the target keeps its own
`default` login; it then saves and restarts to apply the restored configuration.
