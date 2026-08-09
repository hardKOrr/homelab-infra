# 303 — Uptime Kuma wire + unwire

**Status:** built
**Depends on:** 200, 404 (uptime-kuma app)
**Blocks:** auto-registered monitoring per CLAUDE.md

## Problem

Every app is supposed to auto-register a monitor. Both wiring halves were TODO headers;
then they were a REST implementation aimed at an API that does not exist.

## Files

- `ansible/tasks/kuma/open-session.yml` — open + sign in a socket.io session
- `ansible/tasks/kuma/call.yml` — emit one event, read its acknowledgement
- `ansible/tasks/kuma/poll-once.yml` — one long-poll read (looped by the two above)
- `ansible/tasks/kuma/drain.yml` — wait for a server push that lags its acknowledgement
- `ansible/vars/uptime-kuma.yml` — the object names the role creates and the wiring finds
- `ansible/tasks/wiring/uptime-kuma.yml` — implement
- `ansible/tasks/unwiring/uptime-kuma.yml` — implement
- `ansible/roles/uptime-kuma/tasks/main.yml` — Ntfy channel moved onto socket.io
- `ansible/playbooks/maintenance/status.yml` — monitor list moved onto socket.io

## Approach

**Socket.io, over engine.io long polling, with plain `uri` tasks.** Uptime Kuma exposes no
REST API for monitors in ANY version — v2 was chosen on a surface that is not there. Its
Vue front end is served from a catch-all route, so `GET /api/monitors` answers 200
`text/html` and the previous REST implementation was probing a web page. Long polling is
plain HTTP, so no Python socket.io client is needed; avoiding that dependency was the
original reason for rejecting v1.

**Authenticate with the admin credentials, not the API key.** A Kuma API key authorises
`/metrics` and the badge endpoints. It cannot sign a socket in and cannot touch a monitor.

**Wire:** sign in → `getMonitorList` → absent → `add`; drifted → `editMonitor` with the
full monitor object underneath the changed fields; identical → nothing. The Ntfy channel
is resolved by name against the live instance before it is attached.

**Unwire:** sign in → `getMonitorList` → `deleteMonitor` → verify gone.

**Degradation is asymmetric, deliberately.** Unreachable, uninitialised, or
wrong-credentials degrades to a warning on both halves — monitoring is an add-on. A Kuma
that is signed in and then *refuses a monitor* fails the wire (that is a payload defect,
and swallowing it is what cost five days) but only warns on unwire (a stale monitor beats
a removal that aborts half-way).

## Acceptance

- [x] Wire creates monitor with the correct URL and the Ntfy notification channel attached
- [ ] Down/up state changes trigger Ntfy messages
- [x] Re-wire is idempotent
- [x] Unwire deletes the monitor; idempotent on missing
- [x] If Kuma is unreachable, wire fails gracefully (skip with warning rather than abort the whole deploy)

All ticked items were observed against a live Uptime Kuma 2.5.0 on 2026-08-08 and checked
in its database, not just in the acknowledgement. The open item needs a real Ntfy token
and a monitor that actually transitions; the rehearsal used a dummy token.
