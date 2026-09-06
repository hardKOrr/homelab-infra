# Proxmox automation boundary

This directory contains the shared Proxmox provisioning and guest-record tasks. Read this
guide before changing guest ownership, tag-to-inventory behavior, VM or LXC creation,
cloud templates, or node-local execution.

## Execution model

Provisioning plays run on `localhost` and use Proxmox API modules. A task that requires
`pct`, `qm`, or another node-local command delegates only that task to the selected
Proxmox node. Do not target all `proxmox_nodes` and use `run_once` to choose a node; facts
set by that pattern belong to the selected inventory host rather than to `localhost`.

The shared creation seams are `lxc-create.yml`, `vm-create.yml`, and `vm-clone.yml`.
Kubernetes node provisioning uses `vm-clone.yml`; it does not maintain a second VM
creation path.

## Ownership and tag grammar

homelab-infra manages only resources carrying the exact `_+lab` ownership tag. A leading
underscore alone identifies the platform tag namespace; it does not grant ownership.

| Tag | Meaning | Inventory group |
| --- | --- | --- |
| `_+lab` | Resource created and managed by homelab-infra | `lab_managed` |
| `_-<fact>` | Durable machine fact such as `debian`, `docker`, or `k3s` | `lab_fact_<fact>` |
| `_.stack+<name>` | Docker stack host | `lab_stack_<name>` |
| `_.cluster+<name>` | Cluster member | `lab_cluster_<name>` |
| `_.template` | Managed cloud template | `lab_template` |
| `_.shared` | Hosting substrate deliberately shared across estates | `lab_shared` |
| `_<instance>` | Logical hosting unit for an application instance | `lab_app_<instance>` |

`ansible/inventory/proxmox.yml` owns the translation from tags to group names.
`tag-group.yml` translates one tag for dynamic lookups. Keep those expressions equivalent;
do not open-code another translation.

Proxmox templates are filtered out before inventory groups are built, including managed
templates. Template tasks locate them through the Proxmox node instead of dynamic guest
inventory.

## Device passthrough

`attach-shared-device.yml` binds a host device node (an iGPU) into one or more LXC guests —
several guests may hold it at once. `attach-pci-passthrough.yml` and
`attach-usb-passthrough.yml` assign a PCI or USB device to exactly one VM guest, exclusively;
the device leaves the node for that guest, so a second assignment is a preflight failure, not
a reassignment. All three run after `lxc-create.yml` / `vm-create.yml`, never in place of
them, and each asserts the target guest carries the `_+lab` ownership tag before writing
anything. See [`../../../docs/specs/device-passthrough.md`](../../../docs/specs/device-passthrough.md)
for the full contract, including why the shared and dedicated modes are not interchangeable.

## Guest application records

`record-app-on-guest.yml` records an application tag and a marker-delimited notes row. The
logical hosting unit depends on the backend:

- A native application records its own LXC.
- A Docker application records its stack host.
- A Kubernetes application records every VM in its cluster because cluster assignment is
  stable while pod placement can change.

The helper at `ansible/files/proxmox/guest-app-record.py` performs an idempotent,
read-modify-write update so one application does not replace another application's record
or operator-owned notes. Recording is bookkeeping and is best-effort; it does not decide
whether deployment succeeded.

## Verification

Run the checks selected by [`../../../gate/README.md`](../../../gate/README.md).
`gate/test-proxmox-tags.sh` verifies the shared tag translation,
`gate/test-vmid-from-ip.sh` verifies the address-to-VMID seams,
`gate/test-proxmox-api-contract.sh` drives the real `community.proxmox.proxmox` module
and this repository's real dynamic inventory against a job-local HTTPS mock of the
Proxmox REST endpoints they call, to prove ownership-tag filtering, idempotent
create/no-change, and a controlled failure at the API-transport boundary — without a lab
or real credentials, and `gate/test-device-passthrough-contract.sh` proves the ownership
guard and the dedicated-device conflict check in the device-passthrough task files above
against fixture guest configurations, also without a lab.
