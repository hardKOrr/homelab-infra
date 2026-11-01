# Playbooks

Entry points that orchestrate roles and [shared task files](../tasks/README.md). Each
playbook's header comment lists its inputs; Rundeck options map to the same inputs.

| Directory | What lives there |
| --- | --- |
| `apps/` | Deploy `<app>.yml` per catalog app, plus Remove (`remove.yml`); [adding an app](apps/README.md) |
| `maintenance/` | Day-2 operations: backup, restore, status, schedules, decommission ([README](maintenance/README.md)) |
| `stacks/` | Cross-app stack operations: media stack wiring, container rollback, Plex troubleshooting |
| `docker/` | Create a Docker stack host |
| `proxmox/` | Create a standalone VM |

`bootstrap.yml` prepares the platform once; see [Bootstrap](../../README.md#bootstrap).
