# 303 — notes

2026-07-25 — implementation complete against the v2 REST surface, behind a probe;
slice stays in-progress until slice 404 locks the Uptime Kuma version.

What's in:

- `tasks/wiring/uptime-kuma.yml` — probes `GET /api/monitors` first; on 200 it selects
  the monitor by friendly name and POSTs (absent) or PATCHes (drifted url / interval /
  active), attaching `notificationIDList` when `monitoring.notification_id` is present,
  then verifies. On anything other than 200 it prints a warning naming the status and
  skips.
- `tasks/unwiring/uptime-kuma.yml` — same probe, DELETE by id, verify gone; an
  unusable API leaves the monitor in place with a warning telling the operator to remove
  it in the UI.

Decisions:

- **The probe is the whole point.** Uptime Kuma v1 exposes no REST CRUD API (socket.io
  only) and the version is not settled until slice 404. A hard REST dependency would
  fail every app deploy on a v1 lab, so an unusable API degrades to a console warning:
  monitoring is an add-on, never the thing that fails a deploy (this slice's acceptance
  item 5). If 404 lands on v1, this file gets replaced with a socket.io implementation
  (`uptime-kuma-api` called via `command:`), and the probe/skip shape stays.
- **Registry key is `monitoring`, not `uptime_kuma`.** Shape B is role-keyed and
  provider-agnostic, and it had no monitoring role at all; the app playbooks were gating
  on a provider-named `homelabinfra_infra.uptime_kuma`. Added
  `monitoring: {provider, instance, host, token, notification_id}` to
  `ansible/vars/CONTRACT.md` §3 and updated the two callers
  (`playbooks/apps/vaultwarden.yml`, `playbooks/apps/_template.yml`) to gate on
  `homelabinfra_infra.monitoring`. Slice 404 must write that key.
- Response shape is read as `json.monitors | default(json, true)` so both the
  list-wrapped and bare-list forms work.
- no_log on every authenticated request (API key).

Verified: ansible-lint green (production profile); payload typing and monitor-selection
expressions verified with a throwaway render. NOT verified live — every acceptance item
needs a deployed Kuma (slice 404), and the REST surface itself is unconfirmed until
then.
