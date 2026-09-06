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
  guest hostname and the exact device identifier (a host path for a shared device, a PCI
  address or a USB vendor:product/port for a dedicated one) as input. No seam discovers or
  guesses a device; "the first free GPU" is not a valid input.
- **Preflight runs before any write.** Every seam asserts, in order: the guest exists on
  the target node, the guest carries the `_+lab` ownership tag (see
  `ansible/tasks/proxmox/README.md`'s tag grammar — this platform does not bind a device
  into a guest it does not own), the device exists on the node, and — for dedicated mode
  only — the device is not already assigned to a *different* guest. A dedicated device
  already assigned to the SAME guest is a no-op, not a conflict.
- **Guest creation is not duplicated.** Device binding runs after `lxc-create.yml` or
  `vm-create.yml` has produced the guest; it does not create a guest of its own, and it does
  not offer a second way to create one.
- **Removal and recovery detach only the project-owned binding.** Removing a device
  reference this platform wrote never touches a `devN`/`hostpciN`/`usbN` entry it did not
  write, and never removes the guest itself. After removal the host and guest configuration
  are left in a state an operator can inspect directly (`pct config` / `qm config`) — no
  state is hidden in a side file only this platform reads.
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
| Device path/PCI address/USB id not present on the node | both modes | a typo here would otherwise produce a guest that boots with the feature silently absent |
| Device already assigned to a different guest | dedicated only | a PCIe or USB device left the host for one guest; a second guest cannot also receive it |

## Enforced by

- inspection — cite this specification in findings
- `gate/test-device-passthrough-contract.sh` — proves the ownership guard and the
  dedicated-device conflict check against the real expressions in
  `ansible/tasks/proxmox/attach-pci-passthrough.yml` and
  `ansible/tasks/proxmox/attach-usb-passthrough.yml`, without a lab
