# Kubernetes backend

This directory contains shared tasks used by applications hosted on the k3s cluster. The
cluster itself is built by `ansible/playbooks/apps/k3s-cluster.yml` and configured by the
`k3s_cluster` role.

## Boundary

- The k3s cluster is a hosting backend. It is not an application deployment.
- One application instance owns one namespace named `app-<instance>`.
- Deploy, status, backup, restore, and removal use that namespace as the ownership boundary.
- An existing namespace without the homelab-infra ownership label is not adopted.
- Runtime secrets are copied from canonical Vaultwarden items into only the owning
  namespace.

`derive-namespace.yml` owns instance-to-namespace conversion. Consumers must use it rather
than construct namespace names independently.

## Cluster and publishing flow

The cluster playbook provisions each VM through the shared Proxmox clone seam. Its
sequential cluster-foundation play ensures one node initializes embedded etcd and later
nodes join it. Cluster-scoped storage and ingress configuration runs only after all nodes
have joined.

The Kubernetes API, node addresses, and ingress virtual IP remain private. The platform
Caddy is the public TLS edge and proxies a routed application to the stable internal
MetalLB virtual IP. The cluster does not create a second public edge.

## Availability and storage

Read availability from the declared properties, not from the node count:

- `failure_domain_mode` describes whether control-plane members occupy distinct Proxmox
  nodes.
- `storage_class` describes where application volumes live and how they can move.

The current default StorageClass is `homelab-local-path`. The platform owns its local-path
provisioner and disables the bundled k3s local-storage add-on so exactly one default class
exists. Each volume is a directory on one cluster VM. A pod with that volume is pinned to
the node; if the node is unavailable, the pod remains `Pending`. Control-plane quorum does
not make application data highly available.

A redundant NFS service can provide storage that remains reachable after a Kubernetes
node fails, assuming the NFS service, its storage, and the network remain available. The
current role does not deploy an NFS provisioner or migrate existing volumes. Adding NFS
therefore requires a new StorageClass implementation, workload migration, backup and
restore validation, and application-level checks. Shared storage removes the current
node-pinning limit; it does not by itself make a single-replica application or its database
highly available.

The current schema is documented in [`../../vars/CONTRACT.md`](../../vars/CONTRACT.md).
Hosting selection belongs in [`../../playbooks/apps/README.md`](../../playbooks/apps/README.md),
not in this backend guide.
