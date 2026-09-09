# Frigate recovery and device authority

Frigate is a stateful, continuously writing Docker-on-LXC application. Its own guest is
the frigate stack host; it must not be placed on a shared media or services stack.

## Authority before deployment

The operator records these concrete values before the first live run:

- Frigate instance and Proxmox target node
- recording-storage host mount and its guest path
- Coral Proxmox USB resource mapping and its declared shared mode
- iGPU render node and its declared shared mode

The playbook refuses empty device inputs, missing/ambiguous mode declarations, unavailable
paths, conflicting LXC holders, and guests without _+lab. It never discovers or adopts a
camera, USB device, or GPU.

## Recovery boundary

The guest PBS snapshot restores the Docker project and Frigate configuration. The recording
mount is existing node storage, so its owner must provide a retention-sized storage snapshot
or backup. Restore that storage to the same host path, verify the configured free-space floor,
and attach it to the guest path before starting Frigate. Do not treat an empty directory at
the same path as a successful recording restore.

Reconnect the Coral to the mapped physical port and re-run the deploy. The LXC USB seam
resolves the current bus/device node from the mapping and binds only that node. Compose
receives that exact node rather than the whole USB bus. Remove App detaches only
project-owned render/USB bindings; an untagged operator binding is reported and
left in place. If Coral is disconnected, removal leaves the project bind for a reconnect
and retry rather than guessing at a stale bus/device path. The iGPU is a shared render-node
bind, never dedicated PCI passthrough.

Camera and MQTT credentials come from hidden Vaultwarden fields and are never authored in
config/apps/ or printed by Ansible (no_log: true).
