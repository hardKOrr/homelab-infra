# 006 — generate-ip.yml clobbers homelabinfra_instance

**Status:** open
**Depends on:** none
**Blocks:** safe reuse of generate-ip from any future caller

## Problem

`tasks/network/generate-ip.yml:75-86` does a bare `set_fact: homelabinfra_instance: { network: {...} }`. This is the exact anti-pattern CLAUDE.md flags as forbidden — it destroys all sibling keys on `homelabinfra_instance`.

Today it works because every current caller invokes `generate-ip` as the first task that touches `homelabinfra_instance`. That's fragile — any future caller that runs `generate-ip` after `find-or-create-host` (or any other task that has already populated `homelabinfra_instance`) will silently lose state.

**Upgrade (review 2026-07-02): the `default(omit)` half of this is not a future risk — it is an active bug.** Lines 81-84 store the omit placeholder inside the fact today; `tasks/proxmox/lxc-create.yml:41,44,47` and `vm-create.yml:43,46,51` then test those keys with `is defined` (true — the placeholder is a real string) and embed `__omit_place_holder__<hex>` into the rendered `netif`/`net0`/`ipconfig0` strings for any network lacking an explicit gateway/bridge/vlan. Fix per the Approach note below (build optional keys conditionally; no `omit` inside facts). Also tracked as the standing rule in `.claude/specs/namespace-merge-discipline.md`. The allocation loop above these lines (33-73) is separately broken and owned by `.claude/plans/backlog/fix-generate-ip-allocation-loop.md` — coordinate the two changes.

## Files

- `ansible/tasks/network/generate-ip.yml:75-86` — replace bare set_fact with combine(recursive=True)

## Approach

```yaml
- name: Set network instance facts
  ansible.builtin.set_fact:
    homelabinfra_instance: >-
      {{ homelabinfra_instance | default({}) | combine({
        'network': {
          'name': network_name,
          'cidr': network_config.cidr,
          'gateway': network_config.gateway | default(omit),
          'dns_servers': network_config.dns_servers | default(omit),
          'bridge': network_config.bridge | default(omit),
          'vlan': network_config.vlan | default(omit),
          'ip_address': final_ip_address
        }
      }, recursive=True) }}
  run_once: true
```

Watch the `default(omit)` — inside a literal dict for combine, `omit` produces a real `__omit_place_holder__` value rather than dropping the key. Need to build the dict conditionally or filter post-combine. Easiest: build the inner dict via `dict()` and use `default(none)`, then filter `none` values; or just include `default(none)` and live with null leaves.

Test path: call generate-ip after a no-op `set_fact: homelabinfra_instance: { sentinel: 'keep-me' }`, assert `sentinel` still present after.

## Acceptance

- [ ] `generate-ip.yml` uses `combine(recursive=True)`
- [ ] A caller that pre-populates `homelabinfra_instance.sentinel` retains `sentinel` after `generate-ip` runs
- [ ] All existing callers (`create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml`, `find-or-create-host.yml`) still work
