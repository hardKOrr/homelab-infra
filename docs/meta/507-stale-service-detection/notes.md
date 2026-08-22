# 507 — notes

## 2026-08-22 — the fault that produced this slice

Found while executing slice 205, not while looking for it. The sequence:

1. `master` was pushed to `origin/master` (6476ce1) so the runner could see the new
   maintenance jobs.
2. `Reimport Jobs` was started over the Rundeck API. Execution 268 failed immediately:

   ```
   IOFailure: Cannot run program "/bin/sh": Failed to exec spawn helper:
   pid: 71181, exit code: 1
   ```

3. `ls -la /usr/lib/jvm/` showed `java-21-openjdk-amd64` modified 2026-08-22 06:18.
   `systemctl show rundeckd -p ActiveEnterTimestamp` showed the JVM had been running since
   2026-08-17 17:09. An in-place JDK update under a live JVM.
4. `systemctl restart rundeckd` fixed it completely. Every later execution succeeded.

What makes this worth a slice is not the JDK. It is that **every existing signal said the
runner was healthy**:

| Signal | What it said |
|---|---|
| `systemctl is-active rundeckd` | `active` |
| the HTTP port | answered |
| Uptime Kuma | up |
| `/var/run/reboot-required` | absent — a JDK update sets no such flag |
| slice 205's window decision | `homelab-rundeck  clean` |

The lab had been in that state for roughly six and a half hours. Nothing would have
reported it. It was found only because a human went to run a job.

Note the second-order point, which is the reason detection belongs on the guest: the
broken host *was the control plane*. A detector that runs from Rundeck could not have
reported this particular fault, because Rundeck was the thing that could not spawn a
process. The guest publishing to Ntfy from its own timer has no such dependency — the same
argument 205 made for putting the window on the guest.

## Open question carried into implementation

`needrestart` on a Docker host will list container processes running against replaced
images. That is Watchtower's job and this slice must not duplicate it — see the Remaining
box. The line to draw is probably: restart `dockerd` and `containerd` here, and let
Watchtower own everything inside a container.
