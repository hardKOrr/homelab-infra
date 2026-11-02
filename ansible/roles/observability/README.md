# Observability

## Native recovery

Observability native recovery stops only its Prometheus and Grafana Compose services and
archives the complete data directory, preserving the Prometheus TSDB and Grafana's saved
dashboards and alert rules together. Deploy regenerates the config and provisioning files.
Restore checks the selected PBS point and, for an existing target, a separate pre-restore
point; it stages the archive and keeps the original data directory on the same filesystem
until both services pass health checks. A failed replacement puts the original data back
and leaves Observability stopped.
