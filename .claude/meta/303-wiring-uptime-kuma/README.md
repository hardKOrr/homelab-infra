# 303 — Uptime Kuma wire + unwire

**Status:** built
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

- [ ] Down/up state changes trigger Ntfy messages — needs a real Ntfy token and a monitor
      that actually transitions; the rehearsal used a dummy token
- [x] Wire creates the monitor with the correct URL and the Ntfy channel attached
- [x] Re-wire is idempotent
- [x] Unwire deletes the monitor; idempotent on missing
- [x] Unreachable Kuma skips with a warning rather than aborting the deploy

Every ticked item was observed against a live Uptime Kuma 2.5.0 on 2026-08-08 and confirmed
in its database, not just in the acknowledgement.

## Links

- `ansible/tasks/kuma/` — the shared conversation: `open-session.yml`, `call.yml`,
  `poll-once.yml`, `drain.yml`. Any new Kuma reader goes through these.
- `ansible/tasks/wiring/uptime-kuma.yml`, `ansible/tasks/unwiring/uptime-kuma.yml`
- `ansible/vars/uptime-kuma.yml` — object names the role creates and the wiring finds
- `ansible/roles/uptime-kuma/tasks/main.yml` — Ntfy channel, moved onto socket.io
- `ansible/playbooks/maintenance/status.yml` — monitor list, moved onto socket.io
- [notes.md](notes.md) — session narrative, including the REST dead end
