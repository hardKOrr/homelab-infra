# Deemix

## Native recovery

Deemix native recovery stops only the selected service and captures all of
`config_path` (settings and the optional ARL) as one PBS `data.pxar` in
`host/<instance>`. Downloads stay outside the recovery unit. The two newest
snapshots are retained alongside daily retention so a pre-restore safety backup
keeps the prior recovery point. Restore previews until `overwrite=true`, checks
the source group, and keeps the original config on its filesystem until the
replacement passes health checks. Any later failure puts the original back and
starts it. An empty ARL supports settings-only use; the human
route still uses its declared Authentik access path. Deploy preserves an existing
or restored `.arl` when the configured ARL is empty, and publishes that effective
value to the canonical media credential for Lidarr. A non-empty configured ARL
rotates the file and restarts Deemix.

## Kubernetes

With `hosting.method: kubernetes` Deemix gets a namespace of its own, with the config volume on
the cluster's default StorageClass, and `app.download_path` is mounted from the NFS share
that holds it at `/downloads`, the Docker container path, through
`tasks/resolve-kubernetes-media.yml` and `tasks/kubernetes/prepare-media-volumes.yml`.
Deploy sets Lidarr's folder-structure setting and signs in with the ARL through the
ingress address. The cluster instance is not discovered by Wire Media Stack. Its backup
CronJob and restore Job come from the shared Kubernetes recovery files; the download share
is not archived.
