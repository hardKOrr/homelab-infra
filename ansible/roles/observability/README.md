# Observability

## Native recovery

Observability native recovery stops only its Prometheus and Grafana services (Compose
services, or the `prometheus` and `grafana-server` units natively) and
archives the complete data directory, preserving the Prometheus TSDB and Grafana's saved
dashboards and alert rules together. Deploy regenerates the config and provisioning files.
Restore checks the selected PBS point and, for an existing target, a separate pre-restore
point; it stages the archive and keeps the original data directory on the same filesystem
until both services pass health checks. A failed replacement puts the original data back
and leaves Observability stopped.

## Native hosting

With `hosting.method: native` and `proxmox.guest_type: lxc` or `vm`, the instance gets its own
guest. `tasks/native-install.yml` installs Grafana from `apt.grafana.com` and the Prometheus
release binary at the exact versions `vars/app-defaults/observability.yml` pins (the Docker
images use the same tags); `tasks/native-run.yml` runs them under systemd with Grafana's
data and provisioning at the same `data_path` and `config_path` as on Docker, so the shared
backup and restore swap see one layout. The service users have fixed uids for that restore.

## Kubernetes

With `hosting.method: kubernetes`, `tasks/kubernetes.yml` runs Prometheus and Grafana in one
pod, so Prometheus stays on the pod loopback and only Grafana is routed. Each data tree has
its own PVC; the shared backup CronJob and restore Job archive them as `grafana.pxar` and
`prometheus.pxar`. The scrape list is the same snapshot as on Docker, and a changed list
rolls the pod.
