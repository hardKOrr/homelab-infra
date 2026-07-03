# discover-dhcp-lease-ip

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/one-click-idempotent.md; review 2026-07-02

## Goal

When a guest is provisioned on a DHCP network, discover its actual leased IP after boot instead
of propagating the literal string `"dhcp"` into `add_host` and the wiring contract.

## Context

`tasks/network/generate-ip.yml:23-27` short-circuits DHCP networks by setting
`final_ip_address: "dhcp"`, which flows into `homelabinfra_instance.network.ip_address` and then:

- `playbooks/proxmox/create-lxc.yml:31-37` → `add_host: ansible_host: "dhcp"` — Play 2 then
  tries to SSH to a host literally named "dhcp".
- The same value would reach `wiring_upstream_host` in app playbooks.

The create tasks already wait until the guest is exec-ready
(`tasks/proxmox/lxc-create.yml:87-94` via `pct exec`, `tasks/proxmox/vm-create.yml:91-98` via
`qm guest exec`), so a discovery step can run right after: for LXC,
`pct exec <vmid> -- ip -4 -o addr show eth0` (or `pct exec ... hostname -I`); for VM,
`qm guest exec <vmid> -- ...` via the QEMU guest agent (agent is enabled by default,
`vars/homelabinfra-defaults.yml:22`). Parse the address and write it back into
`homelabinfra_instance.network.ip_address` via `combine(recursive=True)` (per
specs/namespace-merge-discipline.md), so every downstream consumer (`add_host`, wiring, VMID
notes) sees a real IP. DHCP+VMID interplay is already handled: `tasks/proxmox/ip-to-vmid.yml`
asserts an explicit vmid when DHCP is configured, and that must stay (the lease isn't known at
VMID-derivation time).

## Acceptance criteria

- After creating an LXC or VM on a DHCP network, `homelabinfra_instance.network.ip_address`
  holds the leased IPv4 address, updated via `combine(recursive=True)`.
- No `add_host` call in `playbooks/proxmox/create-lxc.yml`, `create-vm.yml`, or
  `playbooks/docker/create-docker-host.yml` can receive `ansible_host: "dhcp"`.
- Static-IP networks are unaffected (discovery step skipped or verified equal).
- The DHCP-requires-explicit-vmid assert in `ip-to-vmid.yml` still holds.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
