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
- [x] **Live, 2026-08-09:** a deploy allocated from a pool. Deploy Prowlarr created the
      `media_stack` host at **192.168.0.100**, the first address of the `media_stack` pool,
      inherited by stack name with nothing declared per app — the flat walk would have
      handed out `.0.16`. VMID `168000100` derived from it, tags `homelab-infra;media_stack`
- [x] **Live, 2026-08-09:** the lab's `config/proxmox.yml` migrated onto declared pools.
      The invented `ip_offset: 2305` / `max_hosts: 2560` fence was already gone (the file was
      rewritten 2026-08-03); what it lacked was pools and reserved spans. It now declares
      `platform` (.0.10–.0.49), `sso_stack`, `monitoring_stack`, `media_stack`
      (.0.100–.0.149) and `apps` (.0.200–.0.249, the `default_pool`), plus reserved spans for
      the whole of the operator's older estate — `192.168.1.0/24`, `192.168.3.0/24`,
      `192.168.7.0/24`, `192.168.13.0/24` and `.0.1–.0.9`. `ip_offset: 10` stays as the
      no-pool fallback. Backed up to `config/.backups/proxmox.yml.pre-pools-*` and verified
      to parse identically to the backup apart from the three new keys. The six existing
      guests (.0.10–.0.15) were untouched
- [x] **Live, 2026-08-16:** one deploy from a pinned address. `stacks.pintest_stack.ip_address:
      192.168.0.240` in the runner's `config/infrastructure.yml`, a throwaway
      `config/apps/qbittorrent-pintest.yml` declaring `stack: pintest_stack`, and
      `Deploy qBittorrent` (execution 169) created `pintest-stack` at exactly
      **192.168.0.240/20**, vmid **168000240** derived from it, tagged
      `homelab-infra;pintest_stack`. The pin sits inside the `apps` pool (.200–.249) that
      an undeclared stack falls back to, so pool resolution and the pin agreed rather than
      the pin bypassing the pool. Torn down afterwards — the stack was created for this

## What the live run found

Two defects the offline probe could not reach, both fixed and both on record because the
shape of them matters more than the fix:

- **A stack host could not reach a pool at all.** `find-or-create-host.yml` — the path every
  Docker app takes — called the allocator with nothing but a network name. Pools worked from
  the native-LXC playbooks and from `create-docker-host.yml`, which is exactly what the
  offline probe exercised, so both gates stayed green over a feature that was unreachable
  from most of the platform. Address selection now comes from the same stack-scoped merge as
  sizing, `_stack_sizing`, and reading it from there rather than from
  `homelabinfra_config.proxmox.lxc` is deliberate: that dict survives between chained plays.
- **An inventory entry with no address is not an in-use address.** The first live allocation
  died on `in-use address '' is not an address, range or CIDR`. The collect task guarded on
  `is defined`, which says nothing about the value, and a guest Proxmox reports no address
  for arrived as an empty string. The allocator's strictness is right and stays — it named
  its refusal, which is the whole point of it being Python rather than Jinja.

## The bug the offline probe caught

Worth keeping: `homelabinfra_instance.network` was merged with `combine(recursive=True)`, and
`create-docker-host.yml` calls the allocator twice in one play. The second call came back
wearing the first call's keys — a guest allocated with no pool carried the previous guest's
`pool`, and the same would have happened to `gateway`, `vlan` and `bridge` when two calls
name different networks. Every key in that sub-dict is derived fresh from the network config,
so a stale one is never right; the sub-dict is now replaced wholesale while its siblings under
`homelabinfra_instance` stay merged.

Both gates were green before the probe ran. Syntax-check does not execute a task file — this
is "green is not working" ([LESSONS.md](../../LESSONS.md)) inside a single play.

## Links

- `ansible/scripts/allocate-ip.py` — the decision, with a reason for every refusal
- `gate/test-allocate-ip.sh` — pools, pins, reservations, exhaustion, malformed input
- `ansible/tasks/network/generate-ip.yml` — pool resolution, the script call, instance facts
- `ansible/vars/CONTRACT.md` §2 — `networks.<name>` schema and the selection order
- `config.example/proxmox.yml`, `config.example/apps/_template.example.yml`
