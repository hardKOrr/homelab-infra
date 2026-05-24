# 001 — Implement `config/*.yml` loader in `load-user-vars.yml`

**Status:** open
**Depends on:** 000
**Blocks:** 004 (key naming acceptance test), 200 (write-generated-facts), every playbook that imports `load-user-vars.yml`

## Problem

[tasks/load-user-vars.yml](../../ansible/tasks/load-user-vars.yml) only loads `homelabinfra-defaults.yml` and an optional `user_vars_file`. The CLAUDE.md-documented workflow (copy `config.example/*.yml` → `config/`, run bootstrap) is unimplemented: nothing reads `config/proxmox.yml` or `config/infrastructure.yml`.

`bootstrap.yml` and every app playbook call `load-user-vars.yml`, then assert `homelabinfra_config.proxmox.*` and `homelabinfra_config.infrastructure.*`. Today those asserts fail on any realistic input that follows the documented workflow.

## Files

- `ansible/tasks/load-user-vars.yml` — extend to load `config/proxmox.yml` + `config/infrastructure.yml`
- `ansible/playbooks/bootstrap.yml` — drops `proxmox.host` typo separately (slice 004)
- (reference) `ansible/playbooks/apps/_template.yml:39-43` — already loads `config/apps/<instance>.yml` directly; that pattern stays

## Approach

Extend `load-user-vars.yml` per the merge order in slice 000:

```yaml
- name: Load homelabinfra defaults
  ansible.builtin.include_vars:
    file: ../../vars/homelabinfra-defaults.yml
  run_once: true

- name: Load config/proxmox.yml (if present)
  ansible.builtin.include_vars:
    file: "{{ playbook_dir }}/../../config/proxmox.yml"
    name: _config_proxmox
  failed_when: false
  run_once: true

- name: Load config/infrastructure.yml (if present)
  ansible.builtin.include_vars:
    file: "{{ playbook_dir }}/../../config/infrastructure.yml"
    name: _config_infra
  failed_when: false
  run_once: true

- name: Load user vars from file option (back-compat)
  ansible.builtin.include_vars:
    file: "{{ user_vars_file }}"
  when: user_vars_file is defined and user_vars_file | length > 0
  run_once: true

- name: Merge all sources into homelabinfra_config
  ansible.builtin.set_fact:
    homelabinfra_config: "{{
      homelabinfra_defaults | default({})
      | combine(_config_proxmox | default({}), recursive=True)
      | combine({'infrastructure': _config_infra | default({})}, recursive=True)
      | combine(homelabinfra_config | default({}), recursive=True)
    }}"
  run_once: true
```

Key choices:
- `config/proxmox.yml` keys (`proxmox`, `networks`, `ansible`) land at the top of `homelabinfra_config` — file stays human-readable, no wrapper needed.
- `config/infrastructure.yml` keys (`domain`, `reverse_proxy`, etc.) land under `homelabinfra_config.infrastructure` — matches what bootstrap.yml asserts.
- Missing files do not fail (`failed_when: false`). Asserts in callers catch missing required keys.
- The legacy `user_vars_file` path still works (last-wins), so existing tests using `vars/user-vars-example.yml` keep working until migrated.

## Acceptance

- [ ] `load-user-vars.yml` loads `config/proxmox.yml` and `config/infrastructure.yml` when present
- [ ] Missing config files do not error; required-key asserts in callers catch real misconfigurations
- [ ] After load, `homelabinfra_config.proxmox.api_host`, `.networks.default.cidr`, and `.infrastructure.domain` are populated from the user's `config/` files
- [ ] Existing `user_vars_file` callers still work
