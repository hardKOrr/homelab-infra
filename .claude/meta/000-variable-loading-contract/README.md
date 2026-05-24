# 000 — Variable-loading contract

**Status:** open
**Depends on:** none
**Blocks:** 001, 002, 003, 004, and everything downstream — every slice assumes this contract

## Problem

The CLAUDE.md story ("copy `config.example/*.yml` → `config/`, run bootstrap") is not implemented. Today, `tasks/load-user-vars.yml` only knows about a legacy single-file `user_vars_file` parameter ([vars/user-vars-example.yml](../../ansible/vars/user-vars-example.yml)) that wraps everything in `homelabinfra_config:`. Nothing reads `config/proxmox.yml` or `config/infrastructure.yml`. As a result:

- Code reads `homelabinfra_config.proxmox.api_host` (and similar), but no path populates it from user-edited config.
- `config.example/proxmox.yml` and `config.example/infrastructure.yml` have unwrapped top-level keys (`proxmox:`, `networks:`, `domain:`, etc.) — incompatible with the namespace the code reads.
- `homelabinfra_infra` is loaded from `config/.generated/facts.yml` in [_template.yml:47](../../ansible/playbooks/apps/_template.yml#L47) and read in two contradictory shapes (`.reverse_proxy.provider`, `.domain`) with no spec.

Without a written contract, every subsequent slice will guess at the wrong shape.

## Deliverable

A spec doc — no code. Lives at `ansible/vars/CONTRACT.md` (or similar). Defines:

1. **The three namespaces** (already in CLAUDE.md, restated authoritatively here):
   - `homelabinfra_config.*` — merged user + defaults (input layer)
   - `homelabinfra_instance.*` — computed at runtime
   - `homelabinfra_infra.*` — service endpoints + tokens (loaded from `config/.generated/facts.yml`)

2. **What loads from where into what key**, e.g.:
   - `vars/homelabinfra-defaults.yml` (top-level `homelabinfra_defaults:` wrapper) → seed for `homelabinfra_config`
   - `config/proxmox.yml` (top-level `proxmox:`, `networks:`, `ansible:`) → merged under `homelabinfra_config` (no wrapper in the file — loader injects)
   - `config/infrastructure.yml` (top-level `domain:`, `reverse_proxy:`, etc.) → merged under `homelabinfra_config.infrastructure`
   - `vars/app-defaults/<app>.yml` (`<app>_defaults:` wrapper) → `_app_defaults` local
   - `config/apps/<instance>.yml` → `_instance_config` local, merged into `app_config`
   - `config/.generated/facts.yml` → `homelabinfra_infra` (shape defined below)

3. **`homelabinfra_infra` shape**, e.g.:
   ```yaml
   domain: homelab.example.com          # copied from infrastructure.yml
   reverse_proxy: { provider, instance, host, port }
   sso: { provider, instance, host, token }
   notifications: { provider, instance, host, topic }
   dns: { provider, host, api_key }
   backups: { instance, datastore_path }
   vaultwarden: { host, port }          # populated after bootstrap step 1
   ```
   Each baseline service appends/updates its own subtree via `bootstrap/write-generated-facts.yml`.

4. **Merge order** (precedence low → high):
   1. `homelabinfra-defaults.yml`
   2. `config/proxmox.yml` (wrapped into `proxmox`/`networks`/`ansible`)
   3. `config/infrastructure.yml` (wrapped into `infrastructure`)
   4. `user_vars_file` if present (back-compat — already provides `homelabinfra_config` wrapper)

5. **Required vs optional keys** per file (input validation contract).

## Acceptance

- [ ] Contract doc exists, ratified by reading it against current code
- [ ] Each subsequent foundation slice (001, 002, 003, 004) cites this doc as its source of truth
- [ ] If the contract conflicts with code or examples, the conflict is enumerated and resolved by 001/002/003/004 — not left as an open question
