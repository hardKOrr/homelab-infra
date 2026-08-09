# 303 — Uptime Kuma wire + unwire

**Status:** done
**Subject:** Uptime Kuma
**Related:** 404 (the app role and its initialization), 200 (registry facts)

## Goal

Every app auto-registers a monitor on deploy and drops it on removal. Both halves were
first a TODO header, then a REST implementation aimed at an API that does not exist; the
shipped design drives **socket.io over engine.io long polling with plain `uri` tasks**.

Long polling is ordinary HTTP, so no Python socket.io client is needed — avoiding that
dependency was the original reason for rejecting Kuma v1. Authentication uses the **admin
credentials, not the API key**: a Kuma API key authorises `/metrics` and the badge
endpoints only, and can neither sign a socket in nor touch a monitor.

- **Wire:** sign in → `getMonitorList` → absent → `add`; drifted → `editMonitor` with the
  full monitor object underneath the changed fields; identical → nothing. The Ntfy channel
  is resolved by name against the live instance before it is attached.
- **Unwire:** sign in → `getMonitorList` → `deleteMonitor` → verify gone.
- **Degradation is asymmetric, deliberately.** Unreachable, uninitialised or
  wrong-credentials degrades to a warning on both halves — monitoring is an add-on. A Kuma
  that signs in and then *refuses a monitor* fails the wire (a payload defect, and
  swallowing it is what cost five days) but only warns on unwire, since a stale monitor
  beats a removal that aborts half-way.

## Remaining

- [x] Down/up state changes trigger Ntfy messages — met 2026-08-09 on the lab's own
      instance. Grafana stopped at 03:04:45; Kuma held the failure at status 2 for two
      minutes of retries, then wrote status 0 `important=1` and Ntfy received
      *"observability Down [Uptime-Kuma]"* (priority 5, `red_circle`) at 03:07:02.
      Restart at 03:07:40 produced *"observability Up"* (`green_circle`) at 03:08:02.
      Both messages confirmed in Ntfy's own `cache.db`, not in Kuma's acknowledgement
- [x] Wire creates the monitor with the correct URL and the Ntfy channel attached
- [x] Re-wire is idempotent
- [x] Unwire deletes the monitor; idempotent on missing
- [x] Unreachable Kuma skips with a warning rather than aborting the deploy

The first four were observed against a throwaway Uptime Kuma 2.5.0 on 2026-08-08 and
confirmed in its database. The fifth was observed on the lab's own instance (.0.14) on
2026-08-09, after executions 39/40/41 took it from zero monitors to three.

**Testing the alert path needed a third monitored service.** With only `uptime-kuma` and
`ntfy` registered, the transition is untestable in principle: stopping Ntfy kills the
notifier, stopping Kuma kills the detector. Deploying `observability` supplied a target
whose outage obstructs neither. Any estate that wants to prove this leg needs the same.

## Links

- `ansible/tasks/kuma/` — the shared conversation: `open-session.yml`, `call.yml`,
  `poll-once.yml`, `drain.yml`. Any new Kuma reader goes through these.
- `ansible/tasks/wiring/uptime-kuma.yml`, `ansible/tasks/unwiring/uptime-kuma.yml`
- `ansible/vars/uptime-kuma.yml` — object names the role creates and the wiring finds
- `ansible/roles/uptime-kuma/tasks/main.yml` — Ntfy channel, moved onto socket.io
- `ansible/playbooks/maintenance/status.yml` — monitor list, moved onto socket.io
- [notes.md](notes.md) — session narrative, including the REST dead end
