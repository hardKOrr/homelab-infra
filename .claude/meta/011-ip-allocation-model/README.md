# 011 — IP allocation: a flat +1 walk cannot express a real lab's addressing

**Status:** open
**Subject:** Networking
**Related:** 006 (generate-ip combine)

## Goal

`tasks/network/generate-ip.yml` allocates one way: subnet base, plus a single global
`ip_offset`, walk upward by one, skip what Proxmox already knows, take the first hit. Real
labs do not address that way and this one demonstrably does not — inside a single
`192.168.0.0/20` the live lab is organised by function:

| Band | Contents |
|---|---|
| `192.168.1.x`, `192.168.7.x` | `css-*` media stack, Caddy |
| `192.168.3.x` | `auto-*` application containers |
| `192.168.13.x` | infrastructure — Proxmox nodes, Rundeck, PBS, Ansible |

A flat allocator is blind to that scheme, so the first deploy drops a media app in the
infrastructure band and the scheme decays with every job run. The 2026-07-26 runner setup
already paid the workaround cost: `config/proxmox.yml` needed an invented
`ip_offset: 2305` / `max_hosts: 2560` to fence allocation into an unused-looking band — one
global integer asked to encode a policy that is actually per-app.

**Six addresses (.10–.15) are already allocated under the flat model**, so fix the allocator
before another deploy makes the unwind harder.

Three secondary gaps in the same file: no pinning path for an app that must hold a known
address; `ip_offset` documented as "start allocating from .10", which is true at /24 and
silently means a 12-bit host index at /20; and exclusions drawn only from `proxmox_clients`,
so a NAS, switch, appliance or DHCP range is invisible and allocatable — the lab's gateway
at `192.168.13.1` sits inside the same flat span.

**Planned design:**

- **Named pools inside a network** —
  `networks.default.pools: {infra: {range: "…"}, apps: {…}, media: {…}}`. An app names a
  pool the way it names a stack today; allocation walks that pool only. Absent pool falls
  back to current behaviour, so existing configs keep working.
- **Explicit pin wins** — `network.ip_address` in an instance file used as-is, with a
  conflict assert against the inventory rather than a silent overwrite.
- **Reserved ranges per network** — CIDRs never allocated, so exclusion stops depending on
  Proxmox knowing about a device.
- **Fix the offset wording**, or retire `ip_offset` for pool ranges, which express the same
  intent unambiguously at any prefix length.

Two questions to settle first: whether a pool is declared per app, per stack, or derived
from the stack with a per-app override; and whether the lab's existing three bands should be
codified into `config/proxmox.yml`, which makes the scheme enforced but freezes an
arrangement that grew by hand.

## Remaining

- [ ] An app declaring `pool: media` lands inside the media range on a lab whose next flat
      address would have been outside it
- [ ] A pinned `network.ip_address` is honoured exactly, and a pin colliding with a known
      host fails the run with a named conflict
- [ ] An address inside a reserved range is never allocated, even when Proxmox has no guest
      holding it
- [ ] An existing single-network config with no pools allocates exactly as it does today
- [ ] `config.example/proxmox.yml` describes offset/pool behaviour correctly for a /20

## Links

- `ansible/tasks/network/generate-ip.yml` — pool selection, pinning, reservations
- `ansible/vars/CONTRACT.md` §2 — `networks.<name>.pools` schema
- `config.example/proxmox.yml` — document pools; correct the `ip_offset` wording
- `ansible/vars/app-defaults/<app>.yml`, `_template.yml`,
  `config.example/apps/_template.example.yml` — declare a pool per app
