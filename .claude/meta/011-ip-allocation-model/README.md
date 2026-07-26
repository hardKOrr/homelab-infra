# 011 — IP allocation: a flat +1 walk cannot express a real lab's addressing

**Status:** open
**Depends on:** 006 (generate-ip combine)
**Blocks:** first provisioning run producing addresses the operator wants to keep

## Problem

`tasks/network/generate-ip.yml` allocates one way: take the subnet base, add a single
global `ip_offset`, walk upward by one, skip anything already in the Proxmox inventory,
take the first hit. The only tuning available is where the walk starts and where it stops.

Real labs do not address that way, and this one demonstrably does not. Inside a single
`192.168.0.0/20` the live lab is organised by function:

| Band | Contents |
|---|---|
| `192.168.1.x`, `192.168.7.x` | `css-*` media stack, Caddy |
| `192.168.3.x` | `auto-*` application containers |
| `192.168.13.x` | infrastructure — Proxmox nodes, Rundeck, PBS, Ansible |

That is a deliberate scheme with meaning attached to the third octet. A flat allocator is
blind to it: it drops the next guest wherever the walk lands, so the first deploy puts a
media app in the infrastructure band, or an infra service among the media containers, and
the scheme decays with every job run. The operator's choices are then to hand-edit every
instance file or to let the addressing rot.

Concrete evidence from the 2026-07-26 runner setup: writing `config/proxmox.yml` required
inventing `ip_offset: 2305` / `max_hosts: 2560` to fence allocation into an unused-looking
`192.168.9.x` band. That is a workaround for a missing feature — one global integer being
asked to encode a policy that is actually per-app. It was flagged in the handover as
"review before the first provisioning run," which is the tell that the model is wrong.

Secondary gaps in the same task file:

- **No pinning.** An app that must hold a known address has no `network.ip_address`
  override path; the allocator always decides.
- **`ip_offset` semantics do not survive a large subnet.** Documented in
  `config.example/proxmox.yml` as "start allocating from .10" — intuition that holds for a
  /24 and silently means something else at /20, where the offset is a 12-bit host index.
- **Exclusions come only from `proxmox_clients`.** Anything on the wire that Proxmox does
  not know about — a physical NAS, a switch, an appliance, a DHCP range — is invisible and
  allocatable. The lab's gateway sits at `192.168.13.1`, inside the same flat span.

## Files

- `ansible/tasks/network/generate-ip.yml` — pool selection, pinning, reservations
- `ansible/vars/CONTRACT.md` §2 — `networks.<name>.pools` schema
- `config.example/proxmox.yml` — document pools; correct the `ip_offset` wording
- `ansible/vars/app-defaults/<app>.yml` — declare a pool per app, as stack is declared today
- `ansible/vars/app-defaults/_template.yml`, `config.example/apps/_template.example.yml`

## Approach

- **Named pools inside a network.** `networks.default.pools: {infra: {range: "192.168.13.20-192.168.13.99"}, apps: {...}, media: {...}}`. An app names a pool the
  way it names a stack today; allocation walks that pool only. Absent pool → current
  behaviour, so existing configs keep working.
- **Explicit pin wins.** `network.ip_address` in an instance file is used as-is, with a
  conflict assert against the inventory rather than a silent overwrite.
- **Reserved ranges per network.** A list of CIDRs/ranges never allocated — DHCP scope,
  gateway, appliances — so exclusion stops depending on Proxmox knowing about a device.
- **Fix the offset wording** or retire `ip_offset` in favour of pool ranges, which express
  the same intent unambiguously at any prefix length.

## Acceptance

- [ ] An app declaring `pool: media` lands inside the media range on a lab whose next flat
      address would have been outside it
- [ ] A pinned `network.ip_address` is honoured exactly, and a pin colliding with a known
      host fails the run with a named conflict
- [ ] An address inside a reserved range is never allocated, even when Proxmox has no
      guest holding it
- [ ] An existing single-network config with no pools allocates exactly as it does today
- [ ] `config.example/proxmox.yml` describes offset/pool behaviour correctly for a /20

## Open questions

- **Pool per app, per stack, or both?** Stack membership already groups apps and mostly
  predicts the band; a separate pool key is more expressive but is a second thing to keep
  aligned. Deriving the pool from the stack, with a per-app override, may be enough.
- **Does the live lab want its existing bands codified?** Writing today's three bands into
  `config/proxmox.yml` makes the scheme explicit and enforced — but it also freezes an
  arrangement that grew by hand and may not be exactly what the operator would choose now.
