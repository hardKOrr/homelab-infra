# Maintenance playbooks

This directory contains operator-triggered, read-only, recovery, and scheduled day-2 entry
points. Rundeck exposes the supported operator jobs; the playbooks remain independent of
that interface.

## Operation groups

- Application operations resolve the current hosting target before restart or log access.
  Native applications use their installed `lab-*` helpers; Docker applications act on the
  Compose project; Kubernetes operations use the owning namespace.
- Configuration operations validate, read, or update user-owned runtime configuration.
- Vaultwarden operations enrol accounts, perform the verified Seed-to-Vault cutover, store
  secrets, or enter the explicit recovery path.
- Backup and restore operations use the backend-specific data path rather than treating a
  VM snapshot as application-consistent data.
- Status and ascent verification read state without enforcing drift.

Read [`../../../rundeck/README.md`](../../../rundeck/README.md) for the supported job
projection. Read playbook headers for exact inputs and effects.

## Disruption schedules

`maintenance.schedule` is the single automated-disruption policy. Its schema and
resolution precedence are authoritative in [`../../vars/CONTRACT.md`](../../vars/CONTRACT.md).
The resolution order is global, estate, stack, then application; a narrower declaration
replaces the inherited schedule.

Shared guests use the intersection of their applications' schedules. A `never` schedule
prevents an automated guest reboot, and non-overlapping windows report a conflict.

The resolved schedule becomes `homelab-maintenance.timer` on the guest. The guest acts when
the window opens and only when a reboot is pending. Rundeck does not poll for an open
window. `guest-maintenance.yml` is the operator action that reapplies changed timers,
reports pending reboots, or explicitly forces eligible work.

A user-triggered deployment or maintenance action is explicit operator authority; it is
not an automated event waiting for a maintenance window. Each action must still describe
and enforce its own destructive or disruptive safeguards.

## Whole-lab descent

`lab-descent.yml` arms one-shot systemd timers on the Proxmox nodes and exits before the
runner enters the blast radius. The nodes execute the descent. Proxmox startup ordering
controls the ascent. Run `verify-ascent.yml` afterwards to report guests that did not
return, unreachable guests, and Kubernetes nodes that are not ready.

Do not replace this flow with an Ansible reboot loop. The control plane cannot supervise a
reboot that shuts down its own runner.
