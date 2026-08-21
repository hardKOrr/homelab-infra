# 205 — Maintenance schedules

**Status:** implemented — not yet executed against the live lab
**Subject:** Maintenance schedules
**Related:** 008 (estate contract), 100/201/202 (unattended-upgrades, Watchtower, PBS
schedules this slice unifies), 203 (guest app record), 204 (Kubernetes backend), 503 (lab status)

## Goal

Give every disruptive maintenance action a declared schedule, resolved through the same
override chain the rest of the platform uses, so that reboots and container restarts happen
when the operator decided rather than whenever a timer fired.

Update *cadence* stays continuous — packages and images are still fetched as often as
possible. Only the *disruptive* half is scheduled: guest reboots, container restarts, and
node reboots. One primitive expresses all three cases: a schedule. A permissive schedule is
"reboot whenever it is needed", a narrow one is a maintenance window, and `never` is
notify-only.

Within one resolved schedule, disruption is simultaneous rather than sequenced. A lab that
is wholly down for four minutes is a better outcome than one that is partly broken for
three hours, and sequencing a hundred apps at two minutes each is the latter. Ordering
therefore applies to the ascent, not the descent, and is enforced by Proxmox `startup`
order rather than by a playbook that no longer exists once the reboot begins.

Estate is a hard boundary. A descent computed for one estate never includes another
estate's guests, and the two estates' schedules are independent clocks.

## Remaining

- [x] Proxmox `startup` order populated from the resolved model (commit `caec23d`).
      `tasks/proxmox/resolve-startup.yml` and the `maintenance.boot` config block.
- [x] `maintenance.schedule` accepted in `config/infrastructure.yml`, resolved through
      global → estate → stack → app, with `never` meaning notify-only. Documented in
      `ansible/vars/CONTRACT.md` and `config.example/`. The arithmetic lives in
      `ansible/scripts/maintenance-schedule.py`, with `gate/test-maintenance-schedule.sh`
      covering the modes, midnight and week wrap, the override chain, the shared-host
      intersection, and every refusal.
- [x] `Unattended-Upgrade::Automatic-Reboot "false"` stated explicitly, with
      `-WithUsers` alongside it, in `tasks/bootstrap/configure-unattended-upgrades.yml`.
- [x] `watchtower_schedule` reachable from config, plus `watchtower_monitor_only` for
      `never` and a `TZ` so the cron is read on the lab's clock rather than UTC.
- [x] Tier 1 job — `playbooks/maintenance/guest-maintenance.yml`. Reboots guests
      reporting `/var/run/reboot-required` whose resolved schedule is due, simultaneously
      within a schedule. Runner stays up; it refuses to reboot itself or a Proxmox node.
      Idempotent, with `dry_run` and `force` parameters.
- [x] Kubernetes nodes included: cordon, drain, reboot, uncordon, as one unit
      (`tasks/maintenance/cluster-reboot.yml`). The cold start is named as such in the
      job log rather than implied to be a rolling one.
- [x] Tier 2 job — `playbooks/maintenance/lab-descent.yml` arms a detached one-shot
      systemd timer on each node and exits; `verify-ascent.yml` reports the ascent.
      Nodes are staggered so the CARP pair and the etcd quorum are never lost together.
- [x] Shared-host conflict rule implemented, in the resolver rather than in a playbook:
      a guest's window is the intersection of its apps' windows, one `never` holds the
      whole guest, and disjoint windows are reported as a conflict with both named.

### Not verified by execution

Everything above passes lint, syntax check and the focused resolver tests. Tier 1 has not
been run against the live lab, and Tier 2 has not been armed — arming it reboots every
hypervisor, so it wants a deliberate maintenance evening rather than a build session.
Run `Guest Maintenance` with `dry_run=true` first; it changes nothing and prints the
decision table for every guest.

### Found while doing this, not fixed here

`playbooks/docker/create-docker-host.yml` has **no caller anywhere in the repository**.
It is the only thing that ever deployed Watchtower, so Watchtower is not running on any
stack host — every Docker app role dutifully sets
`com.centurylinklabs.watchtower.enable=true` on a container nothing watches. Stack hosts
are created by `tasks/stack/find-or-create-host.yml`, which never configures it.

Tier 1 now converges Watchtower on every Docker guest it visits, which closes the hole in
practice and is where the schedule belongs anyway. Whether the app-deploy path should also
configure it at creation time is a question for the slice that owns that path.

## Deferred

- Ntfy action buttons that trigger an out-of-band run ("reboot now" from the phone). Ntfy
  supports POST action buttons and the runner can expose a trigger, but it is a Tier 1
  convenience with its own authentication question. Separate slice; the schedule stays the
  authority either way.

## Links
- `config.example/infrastructure.yml` — the `maintenance:` block and its estate overrides
- `ansible/tasks/proxmox/{lxc-create,vm-create}.yml` — `startup` order passthrough
- `ansible/tasks/bootstrap/{configure-unattended-upgrades,configure-watchtower}.yml`
- `ansible/playbooks/maintenance/` — Tier 1 and Tier 2 jobs
- notes.md — session narrative and the decisions behind the single-primitive model
