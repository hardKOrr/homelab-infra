# Shared task files

Reusable flows included by playbooks and roles. Each file's header comment states its
inputs and results. [Shared operations](../README.md#shared-operations) names the file a
role must use for each common operation.

| Directory | What lives there |
| --- | --- |
| `app-wiring/` | App-to-app integration: download clients, *arr registrations, Open WebUI upstreams |
| `backup/` | PBS target resolution and the PBS client (`backup/scripts/pbs-client.sh`) |
| `bitwarden/` | Vaultwarden session checks and item reads and upserts |
| `bootstrap/` | One-time platform setup: PBS, its second copy, unattended upgrades, generated facts |
| `config/` | Config Doctor run and writing user config files |
| `database/` | Database backends, per-app provisioning, dump, restore and drop (`database/scripts/`) |
| `decommission/` | Whole-lab decommission phases |
| `firewall/` | OPNsense base rules, per-app access and the k3s node alias |
| `hosting/` | Hosting-method validation against an app's declared support |
| `kubernetes/` | Cluster workloads and recovery Jobs ([README](kubernetes/README.md)) |
| `kuma/` | Uptime Kuma socket.io session and calls |
| `mail/` | The outbound-mail contract |
| `maintenance/` | Schedules, reboots, lab descent and status helpers |
| `network/` | Address allocation and network resolution |
| `proxmox/` | Guests, placement, devices, tags and ownership ([README](proxmox/README.md)) |
| `recovery/` | Restore method, validation, pre-restore point, target path rebasing and restore swap (`recovery/scripts/`) |
| `stack/` | Docker stack host lookup and creation |
| `unwiring/` | Inverse of `wiring/`, used by Remove |
| `vaultwarden/` | The Seed-mode admin token sink |
| `wiring/` | Proxy, DNS, SSO, monitoring and firewall registration ([README](wiring/README.md)) |

Top-level files are cross-cutting helpers: user config loading (`load-user-vars.yml`),
estate resolution, notifications, degradation reporting, guest bootstrap and scrubbed
failure output. `task-template.yml` is the starting point for a new task file.
