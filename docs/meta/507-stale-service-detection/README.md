# 507 — Stale service detection

**Status:** open
**Subject:** day-2 operations — a guest reports a service running against replaced code
**Related:** 205 (maintenance schedules — owns the guest timer this reuses), 100 and 201
(unattended-upgrades configuration and its Ntfy hook), 503 (Lab Status), 401 (Ntfy)

## Goal

Detect the failure class where a package update replaces code on disk while the process
that loaded it keeps running, and push the finding to Ntfy from the guest itself.

Nothing in the platform sees this today. `unattended-upgrades` installs the new files and
deliberately does not restart anything. The old process keeps serving with the old code
mapped, so the service is `active`, its port answers, and Uptime Kuma reports it up — while
the parts of it that fork, dlopen, or exec are already broken. Only a library or kernel
change sets `/var/run/reboot-required`, so slice 205's window sees a clean guest and
correctly does nothing. The operator finds out at the moment they try to use the service.

This is not hypothetical. On 2026-08-22 unattended-upgrades rewrote
`/usr/lib/jvm/java-21-openjdk-amd64` beneath a `rundeckd` JVM that had been running since
2026-08-17. Rundeck stayed active and answered HTTP; every job failed at dispatch with
`Cannot run program "/bin/sh": Failed to exec spawn helper`. No reboot was pending, no
monitor fired, and the whole runner was unusable until the service was restarted by hand.

Debian already answers the question: `needrestart -b` lists exactly the services running
against replaced binaries or libraries, and separately reports a stale kernel. The slice
installs it and wires its answer into the two seams that already exist, adding no job and
no polling:

1. **Report at the moment staleness is created.** The `unattended-upgrades` systemd drop-in
   already runs `unattended-upgrades-notify` after each run. Extend that path to publish the
   stale-service list with the package count, so one Ntfy message names both what changed and
   what is now running old code.
2. **Fix it inside the declared window.** `lab-maintenance-reboot` currently exits 0 when no
   reboot is pending. Extend it: inside the window, restart the stale services when no reboot
   is pending, and let the existing reboot path subsume them when one is. A service restart is
   a disruption and is scheduled as one — it must never happen outside the guest's resolved
   `maintenance.schedule`, and `never` must hold it exactly as it holds a reboot.

Detection must stay push-driven. An interval job asking every guest "are you stale yet" is
the polling shape 205 rejected, and it would be wrong for the same reason: the guest knows,
the guest has a credential, and the guest already has a timer.

`status.yml` gains the stale-service count as a read-only column, because that is a readout
of state a guest already computed, not a second detector.

## Remaining
- [x] `needrestart` installed and configured non-interactively on every managed guest
      (`$nrconf{restart} = 'l'`) so it reports and never restarts anything on its own
- [x] one Ntfy message after an update names the packages upgraded and the services now
      running replaced code
- [x] a guest with a stale service and no pending reboot restarts only that service, and
      only inside its window
- [x] a guest whose `maintenance.schedule` is `never` reports the stale service and
      restarts nothing
- [x] the Docker case decided and recorded: a stale container runtime is Watchtower's
      domain, a stale `dockerd` is this slice's
- [x] `status.yml` reports stale services per guest
- [x] both gates green

## Docker boundary

`needrestart` is used only for its `NEEDRESTART-SVC` records: stale host systemd units.
It does not enumerate or restart processes inside containers; Watchtower owns those image
updates and their container restarts. `docker.service` and `containerd.service` are host
units, however, so they remain in this slice's list and are restarted by the guest timer
when stale and only when that guest's maintenance window opens.

## Links
- `ansible/tasks/bootstrap/configure-unattended-upgrades.yml` — the notify script and
  drop-in this extends
- `ansible/tasks/maintenance/install-guest-timer.yml` — writes `lab-maintenance-reboot`,
  the window-side actor this extends
- `ansible/playbooks/maintenance/status.yml` — the read-only readout
- notes.md — how the fault was found
