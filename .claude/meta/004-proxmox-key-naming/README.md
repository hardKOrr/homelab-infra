# 004 — Proxmox key naming unification

**Status:** open
**Depends on:** 002 (reconcile schema first — coordinate the key rename with the example file restructure)
**Blocks:** all proxmox-touching work; precondition for 200+ once foundation is in

## Problem

The Proxmox connection config uses two different key names across the repo. A user filling in `config.example/proxmox.yml` cannot run anything because the tasks look for keys that aren't there.

- `config.example/proxmox.yml` and `playbooks/bootstrap.yml` use `proxmox.host` / `proxmox.port`
- `inventory/proxmox.yml`, `tasks/proxmox/lxc-create.yml`, `tasks/proxmox/vm-create.yml`, and `vars/user-vars-example.yml` use `proxmox.api_host` / `proxmox.api_port`

## Files

- `config.example/proxmox.yml:7-8` — declares `host`/`port`
- `ansible/playbooks/bootstrap.yml:41` — asserts `host`
- `ansible/inventory/proxmox.yml:4` — reads `api_host`/`api_port`
- `ansible/tasks/proxmox/lxc-create.yml:8,23-24` — asserts and reads `api_host`/`api_port`
- `ansible/tasks/proxmox/vm-create.yml:7,22-23` — asserts and reads `api_host`/`api_port`
- `ansible/vars/user-vars-example.yml:13-14` — declares `api_host`/`api_port`

## Approach

Pick `api_host` / `api_port` as the canonical name (matches the community.proxmox module's own parameter names and is more explicit). Update `config.example/proxmox.yml` and `bootstrap.yml` to match.

Alternative: pick `host`/`port` — less typing but ambiguous with proxmox node host. Reject.

Steps:
1. `config.example/proxmox.yml`: rename `host` → `api_host`, `port` → `api_port`.
2. `playbooks/bootstrap.yml`: rename the assertion key.
3. Grep for any other stragglers.

## Acceptance

- [ ] `grep -rn "proxmox\.host\b\|proxmox\.port\b"` returns no matches in playbooks/tasks/inventory
- [ ] `config.example/proxmox.yml` uses `api_host`/`api_port`
- [ ] `bootstrap.yml` asserts `api_host`
- [ ] A fresh copy of `config.example/proxmox.yml` → `config/proxmox.yml` lets `create-lxc.yml` proceed past the assert stage in `--check` mode
