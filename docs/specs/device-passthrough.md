# Spec: device passthrough (shared LXC, dedicated VM PCIe/USB)

Some applications need a physical device rather than storage or network: an iGPU for
transcoding or inference, a dedicated GPU for a model server, a USB Zigbee coordinator. This
spec defines the reusable contract both modes share and the constraints that separate them.
It supplements [`ansible/tasks/proxmox/README.md`](../../ansible/tasks/proxmox/README.md),
which remains authoritative for guest ownership and creation.

## Rule

- **Two modes exist, and they are not interchangeable.**
  - *Shared* — a device node (e.g. `/dev/dri/renderD128`) is bound into one or more LXC
    guests. Several guests may share it; binding it into a second guest is not a conflict.
  - *Dedicated* — a PCI or USB device is removed from the Proxmox host entirely and handed
    to exactly one VM. Assigning it to a second guest is a capacity conflict, not a valid
    configuration, because the host itself can no longer see the device.
  - The two modes have separate task seams and separate schemas — see below. Neither
    seam adopts the other's device class.
- **The exact node, guest, and device are explicit before mutation.** Every seam takes the
  guest hostname and the exact device identifier as input: a host path for a shared device,
  a PCI address for a dedicated GPU, or a Proxmox USB resource mapping name for a dedicated
  USB device. No seam discovers or guesses a device; "the first free GPU" is not a valid
  input.
- **A USB device is identified by resource mapping, never by raw vendor:product.** A
  `vendorid:productid` pair names a device MODEL, not one physical unit — two identical
  Zigbee coordinators on the same node are indistinguishable by that pair alone, so it
  cannot answer "is this exact adapter already assigned elsewhere". A Proxmox USB resource
  mapping (`/cluster/mapping/usb`, defined once by the operator against the physical port)
  names one device. This platform assigns and detaches a mapping; it never creates one.
- **Preflight runs before any write.** Every seam asserts, in order: the guest exists on
  the target node, the guest carries the `_+lab` ownership tag (see
  `ansible/tasks/proxmox/README.md`'s tag grammar — this platform does not bind a device
  into a guest it does not own), the device exists on the node, and — for dedicated mode
  only — the device is not already assigned to a *different* guest. A dedicated device
  already assigned to the SAME guest is a no-op, not a conflict.
- **Guest creation is not duplicated.** Device binding runs after `lxc-create.yml` or
  `vm-create.yml` has produced the guest; it does not create a guest of its own, and it does
  not offer a second way to create one.
- **Removal and recovery detach only the project-owned binding.** Every attach seam has a
  matching detach seam — `detach-shared-device.yml`, `detach-pci-passthrough.yml`,
  `detach-usb-passthrough.yml` — that removes exactly the `devN` / `hostpciN` / `usbN`
  entry whose content (host path, PCI address, or USB mapping name) matches the caller's
  input, resolved by reading the guest's current configuration the same way the attach seam
  does. Ownership of a binding is proved by that content match, not by a side ledger: an
  entry belonging to a different device, or one an operator added by hand outside this
  platform, is never touched, and the guest itself is never removed. A detach against a
  guest or index that never held the requested device is a no-op, not a failure. After
  detach the host and guest configuration are left in a state an operator can inspect
  directly (`pct config` / `qm config` for the guest side, `lspci` / `pvesh get
  /cluster/mapping/usb/<name>` for the device side) — no state is hidden in a side file
  only this platform reads. Dedicated PCIe removal stops the guest first, for the same
  hotplug reason attach does; USB and shared-LXC removal hotplug detach on a running guest.
- **Docker-on-VM is the seam Home Assistant needs, not a new application backend.** A
  Docker application already runs unmodified on any guest that has Docker installed and is
  reachable over the inventory connection (`ansible/roles/_template-docker` has no
  guest-type assumption). What makes Home Assistant a VM rather than an LXC is USB/Zigbee
  passthrough itself, not a change to how Docker is deployed onto it.

## Preflight rejections

A preflight failure stops the run before any `pct set` / `qm set` call. Each of these is a
distinct, named assertion, not a single catch-all check:

| Rejection | Applies to | Reason |
|---|---|---|
| Guest not found on the node | both modes | the seam attaches to an existing guest; it does not create one |
| Guest missing the `_+lab` tag | both modes | this platform binds devices only into guests it owns — see the ownership contract |
| Device path/PCI address/USB mapping not present on the node | both modes | a typo here would otherwise produce a guest that boots with the feature silently absent |
| Device already assigned to a different guest | dedicated only | a PCIe or USB device left the host for one guest; a second guest cannot also receive it |

Detach uses the same identifiers to locate and remove a binding, so a typo in a removal call
fails the same "not present" style assertion rather than deleting an unrelated entry.

## Enforced by

- inspection — cite this specification in findings
- `gate/test-device-passthrough-contract.sh` — proves the ownership guard, the
  dedicated-device conflict check, and the detach-matches-only-the-named-binding behavior
  against the real expressions in `ansible/tasks/proxmox/attach-pci-passthrough.yml`,
  `attach-usb-passthrough.yml`, `detach-shared-device.yml`, `detach-pci-passthrough.yml`,
  and `detach-usb-passthrough.yml`, without a lab
