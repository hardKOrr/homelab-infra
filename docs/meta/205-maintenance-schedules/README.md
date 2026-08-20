# 205 — Maintenance schedules

**Status:** open
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

- [ ] `maintenance:` config block accepted in `config/infrastructure.yml`, resolved through
      global → estate → stack → app, with `never` meaning notify-only. Documented in
      `ansible/vars/CONTRACT.md` and `config.example/`.
- [ ] Proxmox `startup` order populated from the resolved model. The key is already a
      passthrough field in `tasks/proxmox/lxc-create.yml` and `vm-create.yml` and is
      currently never set, so guests return from any reboot in arbitrary order — including
      reboots this platform did not cause. Independently shippable and worth shipping first.
- [ ] `Unattended-Upgrade::Automatic-Reboot "false"` stated explicitly in
      `tasks/bootstrap/configure-unattended-upgrades.yml`. It is already the effective
      behaviour because the directive is absent; the slice makes the intent legible and
      guarantees no future package default silently reboots a guest outside its schedule.
- [ ] `watchtower_schedule` reachable from config. It is read by
      `tasks/bootstrap/configure-watchtower.yml` and set by nothing in the repository, so
      every stack host is hardcoded to 04:00 daily. It becomes the container-restart half
      of the resolved schedule.
- [ ] Tier 1 job — guest maintenance. Reboots guests reporting `/var/run/reboot-required`
      whose resolved schedule is due, simultaneously within a schedule. Runner stays up and
      reports the outcome. Idempotent; a run with nothing pending changes nothing.
- [ ] Kubernetes nodes included: cordon, drain, reboot, uncordon. The default
      StorageClass is node-pinned, so a drained pod with a local volume does not move — the
      cluster is one maintenance unit whose reboot is a real workload outage, not a rolling
      one. It is scheduled as a unit, never node-by-node on independent clocks.
- [ ] Tier 2 job — full lab descent, including Proxmox nodes. The runner is inside the
      blast radius, so Ansible arms the descent and exits; the nodes execute it from a
      detached one-shot unit at a wall-clock time. A separate read-only job verifies the
      ascent afterwards. Node access is not new: `tasks/proxmox/attach-host-mounts.yml`,
      `ensure-cloud-template.yml` and `register-nodes.yml` already run node-level commands.
- [ ] Shared-host conflict rule implemented: a guest's reboot uses the most restrictive
      schedule among the apps recorded on it (`tag_app_<instance>` and the notes region
      from slice 203). Container restarts may still be finer-grained than the guest reboot.

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
