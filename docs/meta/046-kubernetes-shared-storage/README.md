# 046 — Kubernetes shared storage

**Status:** built
**Subject:** opt-in NFS CSI shared storage for Kubernetes workloads
**Related:** 204 (Kubernetes hosting backend), 408 (application catalog)

## Goal

Provide a supported, explicitly owned NFS CSI StorageClass without changing the node-local
default or any existing PVC. The role installs the pinned CSI driver, non-default class and
retained snapshot class only when a named target is configured. The backend guide defines target
ownership, Proxmox failure-domain declarations, migration/rollback, workload eligibility, and the
controlled node-loss/snapshot/restore drill.

## Remaining

- [x] Repository contract and focused validation implemented 2026-09-06.
- [ ] Record a live shared-volume, single Kubernetes-node-loss, CSI snapshot, and restore drill
      against the named disposable workload and approved NFS target. No target was supplied to
      this implementation session, so no live storage, PVC, StorageClass, node, or snapshot was
      changed.
- [ ] Each storage-heavy or database workload must prove its own application-consistent backup and
      restore before it names the shared class. Shared storage does not establish database HA.

## Links

- `ansible/roles/k3s_cluster/tasks/storage.yml` — opt-in CSI reconciliation and readiness
- `ansible/tasks/kubernetes/README.md` — storage operation, migration, and drill contract
- `gate/test-kubernetes-shared-storage.sh` — focused static regression coverage
