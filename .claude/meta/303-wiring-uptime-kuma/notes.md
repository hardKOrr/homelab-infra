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

## 2026-07-25 — version question resolved by slice 404

Slice 404 locked **Uptime Kuma v2**, which is the outcome that needs no rework here:
the REST implementation above stands as written, and the socket.io fallback this
slice contemplated is not needed.

What 404 supplies:

- `monitoring: {provider, instance, host, token, admin_user, admin_password}` plus
  `notification_id` — written only when a notification channel was actually
  provisioned, because the wiring task gates on `is defined` and an empty value would
  attach an invalid channel to every monitor.
- `host` is the direct LAN URL (`http://<stack-ip>:3001`), not the public domain —
  during bootstrap neither DNS nor a certificate exists yet.

What still keeps the probe earning its place: Uptime Kuma issues REST API keys only
to a signed-in browser session, so on a first deploy `monitoring.token` is empty
until the operator mints one. The probe-and-skip path is exactly what carries app
deploys through that window. Do not remove it.

Still not verified live — the v2 endpoints this file targets are unconfirmed against
a running Kuma. See 404's notes.md for the specific list to check.

## 2026-08-08 — the REST premise is dead, and socket.io is now proven from Ansible

Slice 404 established two things that decide this slice's direction.

**The REST monitor API does not exist in any Uptime Kuma version.** Choosing v2 to keep
this slice's REST implementation was based on a surface that is not there. `GET
/api/monitors` returns 200 `text/html` because the Vue front end is served from a
catch-all route, so this slice's probe, delete and verify-assert have all been passing
against a page rather than an API — including against an instance that had no database
at all. A valid API key does not change that; the key authenticates `/metrics` and the
badge endpoints, not monitor CRUD.

**Socket.io is drivable from Ansible with no new dependency.** This is the finding that
unblocks the rework. Engine.io v4's long-polling transport is plain HTTP, and 404 now
drives account creation, sign-in and API-key minting with four `uri` tasks each. The
sequence, verified live against 2.5.0:

```
GET  /socket.io/?EIO=4&transport=polling            -> 0{"sid":"..."}
POST /socket.io/?EIO=4&transport=polling&sid=<sid>   body: 40          (attach namespace)
GET  /socket.io/?EIO=4&transport=polling&sid=<sid>   -> 42["setup"] | 42["loginRequired"]
POST ...  body: 420["login",{"username":..,"password":..}]
GET  ...  -> 430[{"ok":true,"token":"..."}]
POST ...  body: 421["addAPIKey",{"name":..,"expires":null,"active":true}]
GET  ...  -> 431[{"ok":true,"key":"uk1_..."}]
```

`42` is an event, the digit after it is the acknowledgement id the reply comes back on,
and `43<id>` is that reply. Every later event on the same socket is authorised once
`login` has been acked.

So the rework is: sign in over a socket, then use Kuma's own monitor events (`add`,
`editMonitor`, `deleteMonitor`, `getMonitorList`) instead of REST, keeping the existing
degradation contract — a monitoring provider must never fail an app deploy.

**Do not take the Python `uptime-kuma-api` dependency.** Avoiding it is why v2 was
chosen over v1, and 404 shows the transport does not require it.
