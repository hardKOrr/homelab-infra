# Navidrome

Navidrome defaults to Docker on the media stack. Setting `hosting.method: kubernetes`
deploys one instance namespace with a default-StorageClass PVC for `/data`. The declared
music library stays on the existing media share: every Kubernetes node must mount that
share at the configured `media_storage.mounts[].path`, and the pod mounts only
`media_storage.library` plus `app.library_subpath` at `/music` as read-only. Deploy checks
the share mount and library directory on every cluster node before creating the namespace.

Both hosting methods use Navidrome's application data as one PBS recovery unit. Docker
uses the shared PBS client and restore swap; Kubernetes uses the shared recovery CronJob
and restore Job. The music share is external to that unit and is never removed or archived.
