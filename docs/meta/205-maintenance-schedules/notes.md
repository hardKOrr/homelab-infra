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

## 2026-08-21 — correction: the schedule enforces itself

The first implementation kept the primitive and then bolted the wrong executor onto it: a
Rundeck job scheduled hourly that woke up, evaluated `due`, and did nothing 23 times a day.
The operator rejected it on sight, and was right on two counts.

**It re-implemented the primitive on top of itself.** We collapsed the `maintenance_class`
enum into a schedule precisely because a schedule already says when. Having a poller ask a
schedule what time it is puts the enum back, wearing a cron.

**It would have poisoned the execution history.** Roughly 8,760 executions a year, almost
all of them no-ops, in the same list an operator scans to find the deploy that broke
something. Every other job here is operator-triggered or weekly.

There is a third count nobody had to raise: it was drift enforcement, in a platform whose
first stated principle is that it does not enforce drift. The tell was already in the same
slice — Tier 2 arms a one-shot systemd timer on each node and exits, because the runner
cannot supervise its own reboot. Tier 1 had the same answer available for a plainer reason
and did not take it.

**The fix.** The resolver also emits `oncalendar`, and
`tasks/maintenance/install-guest-timer.yml` writes `homelab-maintenance.timer` onto the
guest. The guest reboots itself when its window opens, and only if
`/var/run/reboot-required` is there — one stat in the common case. `never` removes the
timer. This is the same shape as unattended-upgrades and Watchtower, which is the house
style: configure the tool, get out of the way. It also survives the runner being down,
which the poller did not.

`guest-maintenance.yml` keeps its place as what an OPERATOR runs — apply the timers after a
config change, report what is pending, and `force=true` to reboot now. `scheduleEnabled` is
false and the comment in the job file says why, so nobody restores the crontab as a
convenience later.

`RandomizedDelaySec=300` on the timer is new and deliberate: every guest on one lab-wide
window would otherwise reboot on the same second. Tier 2 arranges a simultaneous descent on
purpose; a routine kernel update should not trigger one by accident.

**Still open.** The timer is applied by the operator job and (for a fresh guest) at
bootstrap. It is NOT yet re-applied automatically when an app lands on a shared host and
narrows that host's intersected window. The natural seam is
`tasks/proxmox/record-app-on-guest.yml` — one call site, and the exact moment a guest's app
set changes — rather than editing sixteen app playbooks. Not built yet.

## Lesson

An architectural decision that removes a concept has to be carried through to the
*mechanism*, not just the *vocabulary*. Collapsing the enum into a schedule was accepted and
recorded, and the implementation still shipped a poller — which is the enum again, expressed
as an interval. When a decision says "X already expresses this", the test is whether X is
what actually executes.

## 2026-08-22 — first live execution of Tier 1

`master` pushed (6476ce1), `Reimport Jobs` run, `Guest Maintenance` run dry and then for
real. Evidence is in the README's "Executed 2026-08-22" section; three things worth
keeping here.

**The reimport was blocked by a fault this slice cannot see.** Execution 268 failed with
`Cannot run program "/bin/sh": Failed to exec spawn helper`. unattended-upgrades had
replaced the JDK under a `rundeckd` JVM running since 2026-08-17. A restart fixed it
outright. It matters to this slice because Tier 1 looked at that same host and correctly
reported `homelab-rundeck  clean` — no reboot was pending, because a JDK update sets no
such flag. The window mechanism is not wrong; the *pending-reboot* signal is simply not
the only kind of staleness a guest can carry. That gap is now slice 507.

**Cluster nodes get no timer, by design and in fact.** The apply run shows `k3s-1/2/3` at
`ok=3 changed=0` while every other guest changed 4 or 7 items. The cluster is rebooted as
one unit by `cluster-reboot.yml`, so a per-node timer would be exactly the node-by-node
rolling reboot `local-path` makes unsafe.

**`debian-12-cloud` should not be a candidate.** The cloud-init template carries the
`homelab-infra` tag and no address, so both guest probes report UNREACHABLE and are
ignored. Harmless today. The fix is to filter templates out of the candidate list, not to
widen what `ignore_unreachable` covers.

Fixed 2026-08-22, outside the slice, because it turned out to be an inventory fact rather
than a maintenance one. `community.proxmox.proxmox` already publishes `proxmox_template`
and evaluates its `filters` option before `compose`, `groups` and `keyed_groups` run, so
one filter in `ansible/inventory/proxmox.yml` keeps every template out of
`tag_homelab_infra` and `proxmox_clients` — no change at any of the six consumers, and no
per-job exclusion to remember when the seventh is written. The template also now carries
the facet tag `role_template` alongside `homelab-infra`, in the same `<facet>_<value>`
form as `app_<instance>` and `kind_<hosting>`, so it is distinguishable in the Proxmox UI;
`ensure-cloud-template.yml` adds it read-modify-write to a template built before the tag
existed, since that path adopts rather than rebuilds.

The rejected alternative was to retag the template `homelab-infra-template` instead of
`homelab-infra`. It does not work: adoption in `ensure-cloud-template.yml` matches the
anchored regex `(^|;)homelab-infra(;|$)`, so the live template would stop being recognised
and every PBS and k3s deploy would hit the vmid assert. Ownership and facet stay separate
tags, and nothing matches on a prefix of the platform's own tag.

**Verified live, both halves, no manual patching.**

- Execution 273, Lab Status on `9c805c7`: `GUESTS (12 tagged homelab-infra)`, no
  `debian-12-cloud`, nothing unreachable. The lab has **three** of these templates — 9002,
  9003, 9001, one per node — all named `debian-12-cloud`; the inventory keys hosts by name,
  which is why 205 saw one entry rather than three.
- Execution 274, Deploy PBS, was the wrong vehicle for the retag and is worth recording as
  such: `apps/pbs.yml` wraps `ensure-cloud-template.yml` in its `_existing_hosts | length == 0`
  provisioning block, so a converge onto an existing PBS VM skips it entirely.
  `tasks/kubernetes/provision-node.yml` calls it unconditionally, once per node, with
  `proxmox.node` pointed at that node — so Deploy k3s Cluster is the job that exercises it,
  and it reaches every node's template rather than one.
- Execution 275, Deploy k3s Cluster: the retag ran `changed` delegated to pve-host-1, -2 and
  -3 in turn, and all three templates went to `homelab-infra;role_template`. The operator's
  untagged hand-built 9000 on pve-host-3 was not touched.
- Execution 276, the same job again: `Adopt the vmid of the template we already built` still
  returned `ok` on all three — the anchored ownership regex matches inside the two-tag string
  — and the retag reported `skipping` on all three. Idempotent.

## Three templates: not a defect to remove, a drift to close (2026-08-22)

The three `debian-12-cloud` templates the executions above turned up looked like
duplication worth deleting. They are not. Proxmox refuses `qm clone --target` unless the
source VM sits on shared storage — `/usr/share/perl5/PVE/API2/Qemu.pm` on pve-host-1:

```
4366:  "Target node. Only allowed if the original VM is on shared storage."
4421:  # clone only works if target storage is shared
4422:  die "can't clone to non-shared storage '$storage'\n"
```

and `/etc/pve/storage.cfg` has exactly one image-capable storage, `friends-pool-zfs`,
declared without `shared 1` — a separate local ZFS pool on each node. `local` is a `dir`
with `content iso,vztmpl`; both `pbs-*` entries are `content backup`. So one template
cannot provision a VM on another node, all three are in use (9002 → k3s-1, 9003 → k3s-2,
9001 → k3s-3 and PBS), and deleting two would leave two of three cluster nodes unable to
reprovision. Collapsing to one template is a shared-storage decision (NFS or Ceph for every
VM disk in the lab), not an Ansible change.

**The real defect was that the three were not the same image.** All were built from the
moving `.../bookworm/latest/...` URL: 9001 on 2026-08-05, 9002 and 9003 on 2026-08-22
(`ctime` in `qm config`, and Debian's snapshot listing shows `20260805-2561` and
`20260821-2577` as separate builds). `ensure-cloud-template.yml` adopted by name plus tag
and never compared image identity, so k3s-3 ran a Debian snapshot seventeen days older than
its peers and nothing reported it. That is invisible until a node behaves unlike the others.

**What changed.** One template per node stays; a node keeps exactly one.

- The declared image is stamped on the template it built, as
  `homelab-infra-image=<basename>` inside the single-line description. A template whose
  stamp does not match the declared identity is destroyed and rebuilt **at the same vmid** —
  replaced, never accumulated beside a newer one.
- Replacement is keyed on the DECLARED identity, never on upstream drift. Pointing at
  `latest` means the bytes behind one URL change without notice, so a check against the live
  image would rebuild templates unattended on Debian's schedule. Bumping
  `cloud_template.image_url` is now the whole update gesture, and nothing else replaces a
  template.
- `image_url` is therefore pinned to `20260821-2577` in both `app-defaults/pbs.yml` and
  `app-defaults/k3s-cluster.yml`. Debian prunes old snapshot directories after a few weeks;
  when the pin 404s, the newest directory from
  `https://cloud.debian.org/images/cloud/bookworm/` replaces it. A loud download failure is
  the right trade against silently building three different images again.
- Replacing is safe without a pre-check because `vm-clone.yml` sets `full: true` — no guest
  depends on a template's base disk, confirmed on the live pools (`zfs list -o name,origin`
  shows no dataset originating from a `base-90xx-disk-0@__base__`). A hand-made linked clone
  would make `qm destroy` fail loudly, which is the correct outcome.
- `cloud_template.vmid` is now optional. Discovery adopts what this platform already built on
  the target node; otherwise a free vmid in 9000-9099 is chosen there, checked against every
  guest in the cluster rather than only VMs. This reverses the earlier "fail rather than
  choose a slot" rule, whose stated risk was scattering a new template per run — that risk
  depended on discovery being unreliable, and discovery by name plus owner tag on one node is
  not. What the old rule actually produced was a per-node `template_vmid` hand-maintained in
  the k3s node declarations and a PBS default of `9000` that collides with the conventional
  hand-built slot — which is exactly the collision this lab has on pve-host-3. Both are gone;
  an explicit `vmid:` still wins.

The PBS asymmetry recorded above is left as it is. `apps/pbs.yml` calls the template seam
from inside its provisioning branch because a converge onto an existing VM has nothing to
clone; `provision-node.yml` calls it per node because each of the three needs one before its
own clone. Both are correct where they are, and the k3s job remains the one that exercises
the path.
