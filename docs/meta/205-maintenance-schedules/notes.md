# 205 — notes

## 2026-08-19 — where the slice came from

An audit of what maintenance actually does today, asked in a session about treating business
and personal platforms differently. What the code said:

| Mechanism | Timing | Disruption |
|---|---|---|
| unattended-upgrades | stock `apt-daily-upgrade` timer, ~06:00 with a 60-minute random delay, per guest | none — `Automatic-Reboot` is not set at all |
| Watchtower | `WATCHTOWER_SCHEDULE`, default `0 0 4 * * *` | restarts every updated container immediately |
| PBS | `backups.schedule`, default `daily` → 02:00 | none |
| `check-native-updates.yml` | weekly | notify only |

Three findings drove the design:

1. **Reboots never happen.** `/var/run/reboot-required` accumulates indefinitely on every
   guest. Kernel and libc updates are installed but never active, so the lab runs unpatched
   code it believes it patched. This is the actual defect; maintenance windows are the shape
   of the fix, not the motivation for it.
2. **`watchtower_schedule` is unreachable.** Read by the template, set by nothing. Container
   restarts were already effectively random from a service owner's point of view, and
   simultaneous across every stack host.
3. **`startup` is a passthrough key that nothing populates.** `lxc-create.yml` and
   `vm-create.yml` both forward it to Proxmox; no defaults file, app defaults file or task
   ever sets it. Boot ordering is therefore absent for *any* reboot, including ones this
   platform did not cause.

## Decisions

**One primitive, not an enum.** The first draft proposed a `maintenance_class` enum —
`anytime` / `windowed` / `manual`. The operator collapsed it: a schedule already expresses
all three. `anytime` is a permissive schedule, `windowed` is a narrow one, `manual` is
`never`. One concept, resolved through the existing override chain, and no vocabulary to
keep in sync between the config and the code.

**Overrides, not a scope key.** Because the primitive is a schedule and schedules resolve
through global → estate → stack → app, there is no separate question of what the window
attaches to. The earlier "estate-scoped or per-app class?" question dissolved.

**Estate is a hard separation.** Not a default, not strictest-wins across estates — the
point of an estate is that its clock is its own.

**Simultaneous descent, ordered ascent.** Sequenced reboots were considered and rejected on
arithmetic: a hundred apps at two minutes each is a 200-minute window. Everything in one
schedule goes down together. What that leaves is an ascent race — apps starting before
Vaultwarden answers, Caddy answering before its backends exist — which `startup` order
solves at the hypervisor, where it keeps working after the control plane is gone.

**Two tiers.** Guest maintenance keeps the runner up and is the frequent case. A full lab
descent including Proxmox nodes cannot keep the runner up, so Ansible arms it and the nodes
execute it detached. Fire-and-forget fits: make the descent correct and the ascent
automatic, then get out of the way. Do not pretend to supervise what cannot be observed.

## Constraints the Tier 2 design must respect

- `onboot: true` is already the LXC and VM default (`homelabinfra-defaults.yml`), so guests
  do return. Their *order* is the gap.
- The OPNsense CARP pair: rebooting the node holding MASTER moves the VIP; rebooting both
  peers' nodes together leaves the lab without a gateway.
- k3s embedded etcd across three nodes: a simultaneous reboot is a cold cluster start, not a
  rolling one. Survivable, but it is a different operation and must be named as such.
- `local-path` is node-pinned, so draining does not relocate a pod with a local volume.
