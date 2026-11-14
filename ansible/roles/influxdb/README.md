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

## Recovery checks

`playbooks/maintenance/influxdb-canary.yml` seeds or verifies a drill bucket through the
authenticated API. Supply `instance`, `canary_bucket` (prefixed `p2-canary-influxdb-`),
`canary_epoch` (a recent Unix timestamp in seconds) and `canary_action`:
`seed` creates a seven-day bucket and writes three points; `present` checks their exact
values and timestamps plus retention; `absent` proves a fresh instance lacks the bucket.
Keep the same bucket and epoch throughout a recovery drill. The target must be lab-owned.
