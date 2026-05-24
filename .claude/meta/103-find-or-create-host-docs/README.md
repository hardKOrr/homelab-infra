# 103 — Document find-or-create-host implicit state machine

**Status:** open
**Depends on:** 006 (related — both are about implicit ordering)
**Blocks:** nothing (docs only)

## Problem

`tasks/stack/find-or-create-host.yml` mutates `homelabinfra_config` (sets hostname, tags, then `ip_address`) before calling `lxc-create.yml`. The dependency chain is implicit and only obvious to someone who reads all three files together. Future maintainers will struggle.

## Files

- `ansible/tasks/stack/find-or-create-host.yml` — add header comment documenting the contract
- (optional) `ansible/tasks/proxmox/lxc-create.yml` — note its expected inputs in a header comment

## Approach

Add a top-of-file comment to `find-or-create-host.yml` that lays out:
- Required inputs (`stack_name`, `homelabinfra_config.proxmox.lxc.network`, etc.)
- Mutations to `homelabinfra_config` (hostname, tags, ip_address) and `homelabinfra_instance` (.network, .lxc, .stack)
- Output (host added to `app_deploy` group, `homelabinfra_instance.stack` populated)
- The fact that this function is one-shot per play — second call would clash on `_stack_hosts` fact

## Acceptance

- [ ] Header comment exists on `find-or-create-host.yml` explaining the state-machine flow
- [ ] A new contributor can read just the header and understand what variables to set before calling
