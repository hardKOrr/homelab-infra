# Frigate recovery contract

Frigate runs in the dedicated stack-frigate Docker-on-LXC guest. The guest PBS snapshot
covers the Compose project, /opt/<instance>/config, and the generated Frigate config.
The continuously written recording mount is an existing Proxmox-node mount and is not
silently adopted, formatted, or deleted by this role. Its storage owner must provide the
retention-sized snapshot/backup and restore it at the same guest path before redeployment.

Before live binding, record the Frigate instance, Proxmox node, recording mount, Coral USB
resource mapping, and iGPU render node in the operator's acceptance evidence. The app role
does not discover cameras, Coral devices, or GPUs.

## Recovery

1. Restore the named stack-frigate LXC from its PBS snapshot, or create it again with the
   same instance config.
2. Restore the recording-storage snapshot to the declared host path and verify free space
   still satisfies minimum_free_gb and the path is mounted into the guest as recordings_path.
3. Reconnect the Coral to the same mapped USB port. Re-run the Frigate deploy; the LXC USB
   seam resolves the mapping to the current `/dev/bus/usb/<bus>/<device>` node and forwards
   only that node to Compose without adopting another device.
4. Re-run the deploy to prove the version endpoint, camera config, shared render node and
   recording path converge. If a device must be removed, run Remove App first while the Coral
   is connected; it detaches only bindings carrying this platform's provenance tag. If the
   Coral is disconnected, Remove App leaves the exact bind for a reconnect-and-retry rather
   than guessing.


Camera credentials and the optional MQTT password are read from hidden fields in
homelab-infra/apps/<instance> in Vaultwarden. They are rendered only into the root-owned
config.yml; secret-bearing Ansible tasks use no_log: true.
