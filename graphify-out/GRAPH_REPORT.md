# Graph Report - .  (2026-06-11)

## Corpus Check
- Corpus is ~15,856 words - fits in a single context window. You may not need a graph.

## Summary
- 98 nodes · 154 edges · 14 communities (10 shown, 4 thin omitted)
- Extraction: 64% EXTRACTED · 36% INFERRED · 0% AMBIGUOUS · INFERRED: 56 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Wiring & Provider Abstraction|Wiring & Provider Abstraction]]
- [[_COMMUNITY_Project Docs & Agent Layer|Project Docs & Agent Layer]]
- [[_COMMUNITY_Role Templates & Lab Scripts|Role Templates & Lab Scripts]]
- [[_COMMUNITY_Config Hierarchy & App Defaults|Config Hierarchy & App Defaults]]
- [[_COMMUNITY_Host Provisioning Pipeline|Host Provisioning Pipeline]]
- [[_COMMUNITY_Docker Role|Docker Role]]
- [[_COMMUNITY_Guest Bootstrap & Day-2 Ops|Guest Bootstrap & Day-2 Ops]]
- [[_COMMUNITY_Proxmox VMLXC Primitives|Proxmox VM/LXC Primitives]]
- [[_COMMUNITY_Bootstrap State & PBS|Bootstrap State & PBS]]
- [[_COMMUNITY_Semaphore & Rundeck UI|Semaphore & Rundeck UI]]
- [[_COMMUNITY_Native Role Defaults|Native Role Defaults]]
- [[_COMMUNITY_Native Role Metadata|Native Role Metadata]]
- [[_COMMUNITY_Docker Role Metadata|Docker Role Metadata]]
- [[_COMMUNITY_Lab Status|Lab Status]]

## God Nodes (most connected - your core abstractions)
1. `homelab-infra CLAUDE.md Project Docs` - 13 edges
2. `Provider-Conditional Execution` - 12 edges
3. `Wiring Pattern (register app with platform services)` - 9 edges
4. `Three-Layer Config Hierarchy` - 8 edges
5. `Wiring: Caddy` - 7 edges
6. `Unwiring Pattern (inverse of wiring, idempotent removal)` - 7 edges
7. `homelabinfra_infra Variable Namespace` - 7 edges
8. `Idempotent Wiring/Unwiring` - 7 edges
9. `deployment-architect Subagent` - 6 edges
10. `combine(recursive=True) Pattern for Nested Dicts` - 6 edges

## Surprising Connections (you probably didn't know these)
- `Create Docker Host Playbook` --implements--> `Stack Model (Docker Apps Grouped on Shared Hosts)`  [INFERRED]
  ansible/playbooks/docker/create-docker-host.yml → .claude/CLAUDE.md
- `Check Native Updates Playbook` --conceptually_related_to--> `Configure Tools Not Replicate Them`  [INFERRED]
  ansible/playbooks/maintenance/check-native-updates.yml → .claude/CLAUDE.md
- `Docker Template Role Defaults` --implements--> `Three-Layer Config Hierarchy`  [INFERRED]
  ansible/roles/_template-docker/defaults/main.yml → .claude/CLAUDE.md
- `Task Template (task-template.yml)` --references--> `Three-Layer Config Hierarchy`  [INFERRED]
  ansible/tasks/task-template.yml → .claude/CLAUDE.md
- `Config Example: Infrastructure Declaration` --references--> `App Defaults: Vaultwarden`  [INFERRED]
  config.example/infrastructure.yml → ansible/vars/app-defaults/vaultwarden.yml

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Lab Maintenance Script Triad (update-check, restart-app, tail-applog)** — files_lab_update_check, files_lab_restart_app, files_lab_tail_applog, concept_lab_maintenance_scripts [EXTRACTED 1.00]
- **Maintenance Playbooks Drive Lab Scripts on Hosts** — maintenance_restart_app, maintenance_tail_applog, maintenance_check_native_updates, files_lab_restart_app, files_lab_tail_applog, files_lab_update_check [EXTRACTED 1.00]
- **App Deploy Three-Play Pattern (Provision + Deploy + Wire)** — apps_template, concept_wiring_step, concept_wiring_contract, concept_one_click_deployment [EXTRACTED 1.00]
- **Stack Host Provisioning Pipeline: find-or-create-host to generate-ip to ip-to-vmid to lxc-create** — stack_find_or_create_host, network_generate_ip, proxmox_ip_to_vmid, proxmox_lxc_create [EXTRACTED 1.00]
- **Docker Role Install-Configure Pipeline: main to install to config to handler** — docker_tasks_main, docker_tasks_install, docker_tasks_config, docker_handlers_restart_docker [EXTRACTED 1.00]
- **Guest Bootstrap plus Unattended Upgrades plus Ntfy Notification Integration** — tasks_guest_bootstrap, bootstrap_configure_unattended_upgrades, concept_watchtower_ntfy_feedback_loop [INFERRED 0.75]
- **Wiring/Unwiring Symmetric Task Pairs** — wiring_caddy, unwiring_caddy, wiring_nginx, unwiring_nginx, wiring_authentik, unwiring_authentik, wiring_opnsense, unwiring_opnsense, wiring_pihole, unwiring_pihole, wiring_uptime_kuma, unwiring_uptime_kuma [EXTRACTED 1.00]
- **Three-Layer Config Hierarchy (defaults → app-defaults → instance)** — vars_homelabinfra_defaults, app_defaults_template, config_example_apps_template, concept_config_hierarchy [EXTRACTED 0.95]
- **UI-Agnostic Job Runner Parity (Semaphore + Rundeck same jobs)** — semaphore_readme, rundeck_readme, concept_ui_job_parity [INFERRED 0.95]

## Communities (14 total, 4 thin omitted)

### Community 0 - "Wiring & Provider Abstraction"
Cohesion: 0.29
Nodes (20): config/.generated/facts.yml (runtime facts store), homelabinfra_infra Variable Namespace, Idempotent Wiring/Unwiring, Provider-Conditional Execution, Unwiring Pattern (inverse of wiring, idempotent removal), wiring_app_name Input Variable, Wiring Pattern (register app with platform services), Config Example: Infrastructure Declaration (+12 more)

### Community 1 - "Project Docs & Agent Layer"
Cohesion: 0.16
Nodes (19): project-manager Agent, Ansible Requirements (Collections), Adding a New App README, Remove App Playbook, App Playbook Template, homelab-infra CLAUDE.md Project Docs, Configure Tools Not Replicate Them, Fire-and-Forget Provisioning (+11 more)

### Community 2 - "Role Templates & Lab Scripts"
Cohesion: 0.24
Nodes (11): Template Docker Handler: Restart APP_NAME, Template Docker Tasks: Deploy Docker App, Template Native Handler: Restart APP_NAME via systemd, Template Native Tasks: Deploy Native LXC App, Lab Maintenance Scripts Contract, lab-restart-app Script, lab-tail-applog Script, lab-update-check Script (+3 more)

### Community 3 - "Config Hierarchy & App Defaults"
Cohesion: 0.22
Nodes (11): App Defaults Template, App Defaults: Vaultwarden, Three-Layer Config Hierarchy, Per-Instance Config File Pattern, Config Example: App Instance Template, Config Example: Proxmox Connection, Config Example: Radarr Instance, Task Template (task-template.yml) (+3 more)

### Community 4 - "Host Provisioning Pipeline"
Cohesion: 0.33
Nodes (10): combine(recursive=True) Pattern for Nested Dicts, Stack Host Find-or-Create Pattern, VMID Derived from IP Address, Create Docker Host Playbook, Task: Generate IP Address, Task: Derive VMID from IP Address, Task: Create LXC Container, Task: Create VM (+2 more)

### Community 5 - "Docker Role"
Cohesion: 0.29
Nodes (7): Template Docker Role Metadata, Docker Role as Mandatory Dependency for Docker Apps, Docker Role Defaults, Docker Handler: Restart Docker, Docker Tasks: Configure Docker Daemon, Docker Tasks: Install Docker Engine, Docker Role Tasks Main

### Community 6 - "Guest Bootstrap & Day-2 Ops"
Cohesion: 0.40
Nodes (5): Bootstrap Task: Configure Unattended Upgrades, Bootstrap Task: Configure Watchtower, Guest Bootstrap Idempotency via homelab_bootstrapped Fact, Watchtower-Ntfy Feedback Loop for Container Updates, Guest Bootstrap Task: Post-Provisioning Setup

### Community 7 - "Proxmox VM/LXC Primitives"
Cohesion: 0.40
Nodes (5): Deterministic VMID from IP, Create LXC Playbook, Create VM Playbook, ansible-expert Subagent, test-developer Subagent

### Community 8 - "Bootstrap State & PBS"
Cohesion: 0.67
Nodes (3): Bootstrap Task: Configure Proxmox Backup Server, Bootstrap Task: Write Generated Facts, config/.generated/facts.yml Incremental Bootstrap State File

### Community 9 - "Semaphore & Rundeck UI"
Cohesion: 1.00
Nodes (3): Semaphore/Rundeck Job Parity, Rundeck README, Semaphore README

## Knowledge Gaps
- **22 isolated node(s):** `Remove App Playbook`, `Bootstrap Playbook`, `Restart App Playbook`, `Lab Status Playbook`, `Tail App Log Playbook` (+17 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `homelab-infra CLAUDE.md Project Docs` connect `Project Docs & Agent Layer` to `Role Templates & Lab Scripts`, `Config Hierarchy & App Defaults`, `Proxmox VM/LXC Primitives`?**
  _High betweenness centrality (0.373) - this node is a cross-community bridge._
- **Why does `Three-Layer Config Hierarchy` connect `Config Hierarchy & App Defaults` to `Project Docs & Agent Layer`, `Proxmox VM/LXC Primitives`?**
  _High betweenness centrality (0.319) - this node is a cross-community bridge._
- **Why does `App Defaults: Vaultwarden` connect `Config Hierarchy & App Defaults` to `Wiring & Provider Abstraction`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Provider-Conditional Execution` (e.g. with `Wiring Pattern (register app with platform services)` and `Config Example: Infrastructure Declaration`) actually correct?**
  _`Provider-Conditional Execution` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Wiring Pattern (register app with platform services)` (e.g. with `Unwiring Pattern (inverse of wiring, idempotent removal)` and `Provider-Conditional Execution`) actually correct?**
  _`Wiring Pattern (register app with platform services)` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Three-Layer Config Hierarchy` (e.g. with `Task Template (task-template.yml)` and `Docker Template Role Defaults`) actually correct?**
  _`Three-Layer Config Hierarchy` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Wiring: Caddy` (e.g. with `Idempotent Wiring/Unwiring` and `Wiring Pattern (register app with platform services)`) actually correct?**
  _`Wiring: Caddy` has 3 INFERRED edges - model-reasoned connections that need verification._