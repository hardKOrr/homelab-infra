# 011 — IP allocation: pools, pins, reservations, and one network per VLAN

**Status:** built
**Subject:** Networking
**Related:** 006 (generate-ip combine)

## Goal

`tasks/network/generate-ip.yml` allocated one way: subnet base, plus a single global
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

**VLANs are the destination** (decided 2026-08-09). The lab runs flat today and migrates to
VLANs later, so the model has to serve both: named networks each carry their own `vlan` tag,
subnet and gateway, and pools express function bands inside a network for as long as the lab
is flat. An app moves between VLANs by changing one name in its instance file.

**Addressing is static, by decision.** Every guest gets an address this platform chose, so
it can wire, route and monitor that guest afterwards. DHCP happens only where a network's
`cidr` says the literal `dhcp` — never as a fallback when allocation is hard.

## What shipped

- **Named pools inside a network** — `networks.<name>.pools.{<pool>: {range | cidr}}`.
  Selection, highest first: `proxmox.pool` in the instance file, which must exist or the run
  fails; the pool named after the app's `stack`, used only when the network defines it;
  `default_pool`; otherwise the old `ip_offset` walk. That asymmetry between a demanded pool
  and an inherited one is what lets a lab adopt pools one band at a time while every stack
  keeps deploying.
- **The override is per instance, not per app** — it lives in `config/apps/<instance>.yml`,
  the file a deploy job names, so two instances of one app can sit in different pools or
  different VLANs.
- **An exhausted pool fails, naming the pool.** It never spills into the wider subnet:
  spilling is how a guest lands in the wrong VLAN once the estate is segmented.
- **Explicit pin wins** — `proxmox.ip_address` is honoured exactly, or refused with the
  conflict named (already held / reserved / outside the pool).
- **Reserved spans per network** — `networks.<name>.reserved`, each an address, an `a-b`
  range, or a CIDR, so exclusion stops depending on Proxmox knowing about a device. The
  gateway is always reserved whether or not it is listed — the lab's own gateway sat inside
  the span the flat allocator walked.
- **`ip_offset` is documented as what it is**: an index into the host range, not a last
  octet. At a /20, `10` means `x.0.10`.

The decision itself is `ansible/scripts/allocate-ip.py`, not Jinja: the allocator compares
addresses across four sources and has to say *which one* refused a request. A template can
compute the answer but cannot explain its absence, and "No available IPs found in
192.168.0.0/20" is the message that made the flat allocator hard to operate.

## Remaining

- [x] An app declaring a pool lands inside that pool's range on a lab whose next flat
      address would have been outside it — probed offline through the task file: a
      `monitoring_stack` hint allocated `192.168.2.0` where the flat walk offered
      `192.168.0.10`
- [x] A pinned address is honoured exactly, and a pin colliding with a known host, a
      reserved span or the pool boundary fails with a named conflict
- [x] An address inside a reserved range is never allocated, even when Proxmox has no guest
      holding it — including the gateway
- [x] An existing single-network config with no pools allocates exactly as it does today
- [x] `config.example/proxmox.yml` describes offset/pool behaviour correctly for a /20
- [ ] **Live:** one deploy allocating from a pool on the lab, and one from a pinned address
- [ ] **Live:** the lab's own `config/proxmox.yml` migrated off the invented
      `ip_offset: 2305` / `max_hosts: 2560` fence onto declared pools. That file lives on the
      runner, so it is a human edit through the Configure App job, not a repo change. The six
      addresses already allocated under the flat model (.10–.15) keep working — they are
      recorded on their guests, and nothing re-derives them

## The bug the offline probe caught

Worth keeping: `homelabinfra_instance.network` was merged with `combine(recursive=True)`, and
`create-docker-host.yml` calls the allocator twice in one play. The second call came back
wearing the first call's keys — a guest allocated with no pool carried the previous guest's
`pool`, and the same would have happened to `gateway`, `vlan` and `bridge` when two calls
name different networks. Every key in that sub-dict is derived fresh from the network config,
so a stale one is never right; the sub-dict is now replaced wholesale while its siblings under
`homelabinfra_instance` stay merged.

Both gates were green before the probe ran. Syntax-check does not execute a task file — this
is "green is not working" ([../LESSONS.md](../LESSONS.md)) inside a single play.

## Links

- `ansible/scripts/allocate-ip.py` — the decision, with a reason for every refusal
- `.claude/gate/test-allocate-ip.sh` — pools, pins, reservations, exhaustion, malformed input
- `ansible/tasks/network/generate-ip.yml` — pool resolution, the script call, instance facts
- `ansible/vars/CONTRACT.md` §2 — `networks.<name>` schema and the selection order
- `config.example/proxmox.yml`, `config.example/apps/_template.example.yml`
