# 003 — Filter proxmox module params (drop user-defined keys)

**Status:** open
**Depends on:** 000
**Blocks:** any real LXC/VM provisioning run

## Problem

[tasks/proxmox/lxc-create.yml:21-29](../../ansible/tasks/proxmox/lxc-create.yml#L21-L29) and [tasks/proxmox/vm-create.yml:19-31](../../ansible/tasks/proxmox/vm-create.yml#L19-L31) `combine` the entire `homelabinfra_config.proxmox.lxc` (or `.vm`) into the dict passed to `community.proxmox.proxmox` / `proxmox_kvm`. Any key the user adds that isn't a valid module parameter gets forwarded as a module argument. The module will reject those at runtime.

Concrete cases:
- `proxmox.lxc.network: default` (network name reference, not a module param) — user-facing per the schema, will hit the module as `network=default`.
- `proxmox.lxc.ip_address`, `proxmox.vm.ip_address` / `vm.ansible_host` — intermediate values used by other tasks; not module params.
- Anything added to `homelabinfra-defaults.yml` for our own bookkeeping (e.g. `disk_volume`, `password`) needs to be either a valid module param or filtered.

This is a "the code looks like it works because the test inputs happen to be valid module params" issue. It will explode the moment someone adds a non-module key.

## Files

- `ansible/tasks/proxmox/lxc-create.yml:18-29` — build the module-args dict with an allowlist
- `ansible/tasks/proxmox/vm-create.yml:16-31` — same, for `proxmox_kvm`
- (reference) module schemas: `community.proxmox.proxmox`, `community.proxmox.proxmox_kvm` — pin known-valid keys

## Approach

Two viable shapes:

**A — explicit allowlist** (recommended): build the dict from named keys we know the module accepts. Anything outside the allowlist is dropped silently or warned. Most readable, hardest to drift.

```yaml
- name: Build LXC module args
  ansible.builtin.set_fact:
    _lxc_module_args:
      api_host: "{{ homelabinfra_config.proxmox.api_host }}"
      api_port: "{{ homelabinfra_config.proxmox.api_port }}"
      api_token_id: "{{ homelabinfra_config.proxmox.api_token_id }}"
      api_token_secret: "{{ homelabinfra_config.proxmox.api_token_secret }}"
      node: "{{ homelabinfra_config.proxmox.node }}"
      vmid: "{{ homelabinfra_config.proxmox.lxc.vmid }}"
      hostname: "{{ homelabinfra_config.proxmox.lxc.hostname }}"
      ostemplate: "{{ homelabinfra_config.proxmox.lxc.ostemplate }}"
      # ... etc., explicit per module schema
      state: present
```

**B — passthrough with subtraction**: take `homelabinfra_config.proxmox.lxc`, drop a known list of "ours" keys (`network`, `ip_address`, etc.), pass the rest. Less explicit but flexible.

Pick A. The point of the contract (slice 000) is to make the schema readable; an allowlist enforces that.

Both `homelabinfra_instance.lxc.netif`/`nameserver`/`searchdomain` (built in the network-merge step) must also be in the allowlist.

## Acceptance

- [ ] `lxc-create.yml` builds module args from an explicit list of community.proxmox.proxmox parameters
- [ ] `vm-create.yml` builds module args from an explicit list of community.proxmox.proxmox_kvm parameters
- [ ] Adding a non-module key (e.g. `proxmox.lxc.notes_for_humans: "foo"`) to user config does not cause module failure
- [ ] All keys we currently use (`netif`, `nameserver`, `searchdomain`, `pubkey`, `vmid`, `hostname`, `ostemplate`, `cores`, `memory`, `tags`, `features`, `password`, `description`, `onboot`, `storage`/`disk_volume`) are routed to the right module param names
