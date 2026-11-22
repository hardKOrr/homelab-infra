# Shared task files

Reusable flows included by playbooks and roles. Each file's header comment states its
inputs and results. [Shared operations](../README.md#shared-operations) names the file a
role must use for each common operation.

| Directory | What lives there |
| --- | --- |
| `apps/` | Loading an app instance's merged config (`load-config.yml`), the restart handler body (`restart.yml`) and the resolve and guest firewall steps of `playbooks/apps/deploy.yml` (`deploy-resolve.yml`, `deploy-firewall.yml`) |
| `app-wiring/` | App-to-app integration: download clients, *arr registrations, Open WebUI upstreams |
| `backup/` | PBS target resolution and the PBS client (`backup/scripts/pbs-client.sh`) |
| `bitwarden/` | Vaultwarden session checks and item reads and upserts |
| `bootstrap/` | One-time platform setup: PBS, its second copy, unattended upgrades, generated facts |
| `config/` | Config Doctor run and writing user config files |
| `database/` | Database backends, per-app provisioning, dump, restore, drop and the drill canary (`database/scripts/`) |
| `drill/` | Recovery drill canary helpers: a command in the app's runtime, the app's URL from the runner, a marker file inside a pod, the Authentik drill owner a browser canary signs in as |
| `decommission/` | Whole-lab decommission phases |
| `firewall/` | OPNsense base rules, per-app access and the k3s node alias |
| `hosting/` | Hosting-method validation against an app's declared support |
| `kubernetes/` | Cluster workloads and recovery Jobs ([README](kubernetes/README.md)) |
| `kuma/` | Uptime Kuma socket.io session and calls |
| `mail/` | The outbound-mail contract |
| `maintenance/` | Schedules, reboots, lab descent and status helpers |
| `network/` | Address allocation and network resolution |
| `proxmox/` | Guests, placement, devices, tags and ownership ([README](proxmox/README.md)) |
| `recovery/` | Restore method, validation, pre-restore point, target path rebasing and restore swap (`recovery/scripts/`); the phases of Restore App (`restore-app-*.yml`) and Restore Guest (`restore-guest-*.yml`) |
| `removal/` | The phases of Remove App (`playbooks/apps/remove.yml`), in run order from its header |
| `stack/` | Docker stack host lookup and creation, and an app's Compose deploy (`compose-deploy.yml`) |
| `unwiring/` | Inverse of `wiring/`, used by Remove |
| `vaultwarden/` | The Seed-mode admin token sink |
| `wiring/` | Proxy, DNS, SSO, monitoring and firewall registration ([README](wiring/README.md)) |

Top-level files are cross-cutting helpers: user config loading (`load-user-vars.yml`),
estate resolution, notifications, degradation reporting, guest bootstrap and scrubbed
failure output. `task-template.yml` is the starting point for a new task file.
