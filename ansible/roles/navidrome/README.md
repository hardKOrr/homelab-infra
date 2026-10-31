# Navidrome

Navidrome defaults to Docker on the media stack. Setting `hosting.method: kubernetes`
deploys one instance namespace with a default-StorageClass PVC for `/data`. The declared
music library stays on its existing NFSv4 share: Deploy resolves the source behind the
configured `media_storage.mounts[].host` on the configured Proxmox node, then gives the pod
only `media_storage.library` plus `app.library_subpath` at `/music` as a read-only NFS
volume. Kubernetes node access to the NFS endpoint is opened through the cluster-node
firewall alias.

Both hosting methods use Navidrome's application data as one PBS recovery unit. Docker
uses the shared PBS client and restore swap; Kubernetes uses the shared recovery CronJob
and restore Job. The music share is external to that unit and is never removed or archived.
