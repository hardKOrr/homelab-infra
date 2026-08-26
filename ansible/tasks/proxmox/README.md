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
`gate/test-proxmox-tags.sh` verifies the shared tag translation, and
`gate/test-vmid-from-ip.sh` verifies the address-to-VMID seams.
