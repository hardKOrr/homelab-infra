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
- **Every binding carries a durable, project-owned provenance tag; content match alone is
  never proof of ownership.** A `devN`/`hostpciN`/`usbN` value matching the caller's input
  shows only that SOME actor bound that device — an operator can create the identical
  entry by hand, outside this platform, and the two are indistinguishable by content.
  Every attach seam therefore also writes a `_.dev+<slug>` guest tag, one per bound
  device, where `<slug>` is the device's host path / PCI address / USB mapping name
  lowercased with every non-alphanumeric run collapsed to a single `-`. The tag is
  guest-inspectable (`pct config` / `qm config` show it in `tags:`, the same way the
  `_+lab` ownership tag is), requires no side ledger, and is what "project-owned" means
  operationally in this contract.
  - **The tag is written only at the moment of binding, never retroactively.** Each attach
    seam writes the `_.dev+<slug>` tag in the SAME `pct set` / `qm set` call that performs
    the bind or assignment, gated on the same condition that gates the bind itself (the
    device was absent before this run). A device or mapping that was already present
    before this run — whether bound by this platform on an earlier run or by an operator
    by hand — is never tagged after the fact merely because attach happened to run again
    and found nothing to do. This is what keeps a pre-existing, untagged, operator-created
    binding permanently unowned rather than silently adopted on the next attach.
  - **Bind and tag land together, or neither does.** Because both are arguments to one
    Proxmox API call, there is no window in which a device is bound but its provenance tag
    write is still outstanding. A failed call leaves the device absent, exactly as before
    the run, and a retry attempts the whole bind-and-tag operation again — there is no
    separate recovery step or reconciliation pass needed for a tag that failed to write.
- **Removal and recovery detach only a binding this platform can prove it made.** Every
  attach seam has a matching detach seam — `detach-shared-device.yml`,
  `detach-pci-passthrough.yml`, `detach-usb-passthrough.yml` — and each first asserts the
  guest carries `_+lab`, exactly like attach. A `devN`/`hostpciN`/`usbN` entry is removed
  only when BOTH are true: its content matches the caller's input, AND the guest carries
  the matching `_.dev+<slug>` provenance tag. A content match with no provenance tag —
  the identical device bound by an operator, by hand — is left in place and reported as
  "not project-owned", never deleted; this is the non-adoption guarantee applied to
  removal, not only to creation. Detach removes the specific provenance tag along with the
  entry, and never touches the guest's other tags, its other `devN`/`hostpciN`/`usbN`
  entries, or the guest itself. A detach against a guest or index that never held the
  requested, project-owned device is a no-op, not a failure. After detach the host and
  guest configuration are left in a state an operator can inspect directly (`pct config` /
  `qm config` for the guest side, `lspci` / `pvesh get /cluster/mapping/usb/<name>` for the
  device side). Dedicated PCIe removal stops the guest first, for the same hotplug reason
  attach does; USB and shared-LXC removal hotplug detach on a running guest.
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

Detach uses the same identifiers to locate a binding, so a typo in a removal call fails the
same "not present" style assertion rather than deleting an unrelated entry. Detach adds two
rejections/no-ops of its own, both in `ansible/tasks/proxmox/detach-*.yml`:

| Outcome | Reason |
|---|---|
| Guest missing the `_+lab` tag → refused | this platform modifies only guests it owns, on removal exactly as on creation |
| Content matches but the `_.dev+<slug>` provenance tag is absent → left in place, reported, not a failure | the entry may be an operator's, created by hand outside this platform; content alone cannot prove otherwise |

## Enforced by

- inspection — cite this specification in findings
- `gate/test-device-passthrough-contract.sh` — proves the ownership guard on every attach
  AND detach seam, the dedicated-device conflict check, and that detach treats a content
  match with no provenance tag as unowned rather than adopting it, against the real
  expressions in `ansible/tasks/proxmox/attach-pci-passthrough.yml`,
  `attach-usb-passthrough.yml`, `detach-shared-device.yml`, `detach-pci-passthrough.yml`,
  and `detach-usb-passthrough.yml`, without a lab
