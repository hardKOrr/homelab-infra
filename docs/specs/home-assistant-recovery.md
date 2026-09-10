# Home Assistant recovery and USB authority

Home Assistant is a single Docker service on its own Debian VM. The VM is a hosting unit,
not a shared stack host, because the Zigbee coordinator is a dedicated Proxmox USB resource
mapping. The mapping is declared by the operator in `config/proxmox.yml` with
`mode: dedicated`, `kind: usb`, and the exact mapping name in `identifiers`. The deploy
assigns that existing mapping; it never creates a mapping, accepts a raw `vendorid:productid`
pair, discovers a serial device, or adopts an untagged binding.

Before live deployment, record:

- the Home Assistant instance and Proxmox target node;
- the VM name/VMID and guest serial path (for example `/dev/ttyUSB0`); and
- the exact Proxmox USB resource mapping and its physical port.

## Config/state backup and restore

The application-consistent `Backup Home Assistant` action stops only the Home Assistant
Compose service and archives the entire configured `/config` directory to PBS as one
`data.pxar`. That includes Home Assistant's SQLite state, `.storage` integration state,
configuration, and the generated `secrets.yaml`. It does not back up the physical USB
coordinator or its Proxmox mapping.

Use `Restore Home Assistant` in plan mode first to list PBS snapshots. With an explicit
snapshot and `overwrite=true`, the action stops Home Assistant, validates and extracts the
archive into private staging, replaces `/config`, starts the service, and waits for HTTP
health. A failed destructive restore leaves the service stopped for inspection. Restore
the VM from its PBS snapshot or redeploy the same instance first; then use the application
restore action for the selected config/state archive.

## USB detach and rebind

Removal stops the Compose service before calling the dedicated USB detach seam. Detach
removes `usbN` only when both its mapping content and the guest's `_.dev+<mapping>` provenance
tag prove that homelab-infra created the binding. A matching untagged operator binding is
reported and left in place. If the coordinator is disconnected, leave the mapping attached
until it is reconnected or detach it manually in Proxmox; the platform never guesses a
replacement device.

After a VM restore or move, confirm that the named mapping still resolves to the intended
physical port on the target node, reconnect the coordinator, and re-run Deploy Home
Assistant. The preflight then checks the managed VM, the mapping's node presence, and
exclusive assignment before the serial path is passed to the container.
