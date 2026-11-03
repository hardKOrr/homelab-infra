# Bazarr

Bazarr defaults to Docker on the `media` VM stack. An instance can select a
Docker LXC stack through the [instance example](../../../config.example/apps/bazarr.example.yml);
stack provisioning, media mounts and wiring use the same shared operations for both guest types.

With `hosting.method: native` Bazarr runs from the pinned upstream `bazarr.zip` release
(`app.native_version`) in a virtualenv under a systemd unit, on a dedicated LXC or VM chosen
by `proxmox.guest_type`; `app.image` pins the same version for Docker. `restore-guest.yml`
serves both guest kinds.

With `hosting.method: kubernetes` Bazarr runs in its own namespace: the config volume
uses the cluster's default StorageClass, and each `app.mounts` entry is mounted from the
NFS export the Proxmox node already uses, at the same container path, through
`tasks/kubernetes/resolve-media-nfs.yml` and `prepare-media-volumes.yml`. A Kubernetes
instance has no stack host, so Wire Media Stack does not discover it. Backup and restore
use the shared Kubernetes recovery Job; the shares are not archived.

## Native recovery

Bazarr native recovery stops only the selected service and archives its config directory
as one PBS `data.pxar` in `host/<instance>`, excluding `log` and `cache`. Backup retains
the two newest snapshots alongside daily retention, preserving the selected recovery
point when the fresh instance is backed up. Restore previews until `overwrite=true`,
checks the source group, and retains the original directory on the same filesystem until
an authenticated settings check and canonical API-key publication succeed. A failure
puts the original state back and starts it. The final Deploy keeps the
restored API key, language profiles and settings.
