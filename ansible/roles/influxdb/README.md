# influxdb

InfluxDB OSS v2 backend, run natively on its own guest unless the config selects Docker.

## Docker

`hosting.method: docker` with `stack: <name>` runs the official image (`app.image`) under
Compose on a stack host. `/opt/<instance>` holds the Compose file and `data/`, the Bolt
metadata, SQLite and engine directory, owned by the image's uid 1000 and mounted at
`/var/lib/influxdb2`. Setup, credentials and the health check use the published `app.port`
exactly as on the native method. Without `app.memory` overrides the cache is sized from
the stack host's total memory, because the host is shared. Recovery archives the same
data directory with the container stopped, then swaps `/opt/<instance>/data`.
