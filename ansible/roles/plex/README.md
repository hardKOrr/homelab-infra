# Plex

## Native recovery

Plex native recovery captures its stopped service's server identity, library databases,
Metadata and Media as one PBS `data.pxar` in `host/<instance>`. Cache, Codecs, Crash
Reports and Logs are excluded; external media mounts and transcode are outside the
recovery unit. Restore requires `overwrite=true`, accepts only snapshots in the selected
`host/<backup id>` group, refuses a state path that overlaps declared media or transcode,
swaps the state directory through the shared restore swap, and publishes the restored
server token to `homelab-infra/media/<instance>` after an authenticated API check. A
failure puts the original state back and starts it.

## Kubernetes

`hosting.method: kubernetes` runs Plex from `tasks/kubernetes.yml`: `/config` on a PVC, the
media shares mounted over NFS read-only at their Docker paths, and, with `app.gpu_device`
naming a k3s node's iGPU, the pod pinned to that node with `/dev/dri`. The claim token
reaches the pod through a Secret for the first start only. Recovery archives the config
PVC; restore goes through the shared Kubernetes recovery Job and publishes the restored
token the same way.
