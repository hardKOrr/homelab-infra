# influxdb

InfluxDB OSS v2 time-series backend. Native by default; Docker when the config asks for it.

## Docker

Set `hosting.method: docker` and `stack: <name>`. The official image (`app.image`) then
runs under Compose on that stack host. In `/opt/<instance>`, `data/` holds the Bolt
metadata, SQLite and engine directory (owner uid 1000, mounted at `/var/lib/influxdb2`)
beside the Compose file. Setup, credentials and the health check use the published
`app.port` as the native method does. Without `app.memory` overrides the cache is sized
from the stack host's total memory, since that host is shared. Recovery archives the same
data directory with the container stopped, then swaps `/opt/<instance>/data`.

## Kubernetes

Setting `hosting.method: kubernetes` runs the upstream image (`app.image`) as a Deployment in
the instance's namespace. The entrypoint runs the first setup from a Secret holding the
Vaultwarden credentials; the data volume (`app.storage` Gi) uses the cluster's default
StorageClass; the tuning is a ConfigMap. A MetalLB LoadBalancer Service serves the API on
the ingress address at `app.port`, and Caddy proxies to it. Deploy stops when another
Service holds that port, and it opens the port for every registered consumer (`influxdb_host`
and `influxdb_port` in its item). It also stops when a data volume exists without its
Vaultwarden credentials.

The recovery point is `database.pxar`: `influxdb.tar`, an `influx backup` of every bucket
and the metadata, plus the ConfigMap. Restore runs `influx restore --full` into the running
server, so the target takes on the source's users and operator token (a new target records
them in Vaultwarden), then restarts to apply the restored tuning.

## Recovery checks

`playbooks/maintenance/influxdb-canary.yml` seeds or verifies a drill bucket through the
authenticated API. Supply `instance`, `canary_bucket` (prefixed `p2-canary-influxdb-`),
`canary_epoch` (a recent Unix timestamp in seconds) and `canary_action`:
`seed` creates a seven-day bucket and writes three points; `present` checks their exact
values and timestamps plus retention; `absent` proves a fresh instance lacks the bucket.
Keep the same bucket and epoch throughout a recovery drill. The target must be lab-owned.
