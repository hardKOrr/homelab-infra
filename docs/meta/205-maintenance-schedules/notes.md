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

## 2026-08-21 — implementation

Built the whole remaining checklist. The decisions above survived contact with the code;
these are the ones the implementation added.

**The primitive is set arithmetic, so it lives in a script.** `always`, `never` and a
window are all one object — a set of minutes-of-week — and the reported mode is the only
thing that tells them apart. That collapses midnight wrap, week wrap, multi-day windows
and the shared-host intersection into one mechanism with no special cases. It is a script
rather than Jinja for the reason `allocate-ip.py` is: a template can compute the answer
but cannot explain why there is none, and "these two apps declared windows that never
overlap" is the message the operator needs. `gate/test-maintenance-schedule.sh` covers it.

**A layer replaces, it never merges.** Half-merging two windows produces a third window
nobody declared. Same call as `resolve-estate.yml` makes for `sso` and `dns`, for the same
reason.

**An empty intersection is a conflict, not a coin toss.** Two apps on one guest with
disjoint windows produce mode `never` and a message naming both. Picking a winner would
reboot a service outside the window its owner was promised, which is precisely the failure
the primitive exists to prevent. It is recorded as a *non-fatal* degradation: the run still
reboots every other guest, and the report names the conflict so it gets fixed.

**Two structural exclusions that `force` does not override.** Tier 1 refuses to reboot the
runner (its own addresses, from gathered facts) and refuses to reboot a cluster node singly.
`force` overrides the *window* and nothing else — a forced run that killed the job reporting
it, or that took an etcd member down outside the cordon path, would be a different and worse
operation than the one asked for.

**The clock is the lab's, not the runner's.** `homelabinfra_config.timezone` is passed to
the resolver, and to the Watchtower container as `TZ`. Without it a runner left on UTC would
open a 03:00 window at what the operator experiences as 21:00, and Watchtower — whose
container defaults to UTC — would restart containers at a third time again.

**Tier 2 arms and exits.** Each node gets `/usr/local/sbin/homelab-descent`, a one-shot
unit, and a timer with an absolute `OnCalendar`. `Persistent=false` deliberately: a node
that was down when its firing time passed must not descend the moment it returns, because
by then the descent it belonged to is over. The script disables its own timer as its first
line, so no path can leave a descent armed for the next boot.

**Nodes are staggered even though guests are not.** The CARP pair and the etcd quorum both
break if two nodes go together, and both constraints were already written down here. The
stagger default is 15 minutes and must exceed a node's full down-and-up cycle, or it
collapses back into a simultaneous reboot.

**The default schedule is nightly 04:00 +120m on purpose.** That is exactly when Watchtower
already restarted containers before the key existed, so a lab that says nothing keeps the
behaviour it had — and gains the guest reboot it never had.

## Found while implementing: Watchtower is deployed nowhere

`playbooks/docker/create-docker-host.yml` is the only thing in the repository that installs
Watchtower, and **nothing calls it**. Stack hosts come from `tasks/stack/find-or-create-host.yml`,
which never configures it. Meanwhile eight app roles set
`com.centurylinklabs.watchtower.enable=true` on their containers, opting in to an updater
that is not running. The whole "Watchtower updated X → Kuma says X is DOWN → run Rollback"
feedback loop in AGENTS.md has therefore never been able to fire.

`watchtower_schedule` being unreachable was the visible half of this; the invisible half was
that there was nothing to reach. Tier 1 now converges Watchtower on every Docker guest it
visits, which is where a *schedule* belongs and which closes the gap in practice. Whether
the app-deploy path should also configure it at creation time belongs to the slice that owns
that path — this one deliberately did not go widen it.
