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

## 2026-08-08 (later) — reworked onto socket.io and exercised live

Implemented and verified against a throwaway Uptime Kuma 2.5.0 (same image as the lab's,
run on the monitoring-stack host on port 3999 and removed afterwards). Every claim below
was checked in Kuma's sqlite database, not read off an acknowledgement.

### What shipped

Four shared task files under `ansible/tasks/kuma/`, because the role and both wiring
halves were all going to need the same conversation:

| File | Does |
|---|---|
| `open-session.yml` | handshake, attach namespace, sign in, wait for the notification list. Never fails |
| `call.yml` | emit one event, poll until its `43<ack>` frame, decode it |
| `poll-once.yml` | one long-poll read: split frames, pong, accumulate, notice a dead transport |
| `drain.yml` | poll until a named server PUSH lands |

Plus `ansible/vars/uptime-kuma.yml`, so the channel and key names have one definition that
both the role and the wiring can read (a role default cannot reach the wiring).

### Four defects the live rehearsal found that no gate could

1. **`'{{ "\x1e" }}'` is not an escape.** Jinja passed the four literal characters through,
   so the frame separator never matched, nothing ever split, and every acknowledgement was
   invisible. Fixed by leaving the escape to the regex engine —
   `regex_findall('[^\x1e]+')` — which does interpret it.
2. **A read-only poll loop kills its own socket.** Engine.io pings every 25s and closes
   the session when no pong comes back; observed dying two polls after the ping. Every
   poll now answers a `2` with a `3`.
3. **The sign-in acknowledgement is queued BEFORE the account's lists.** So a session that
   stops polling at the login ack may never see `notificationList`. This is not
   theoretical — it created a SECOND notification channel with the same name, because the
   existing one looked absent. `drain.yml` exists for exactly this. Note the asymmetry:
   `getMonitorList` awaits its push *before* calling back, so monitors never need it.
4. **`regex_search(...) | first` raises on no match.** An Uptime Kuma still on its setup
   screen serves the setup page for every path including `/socket.io/`, so the handshake
   has no sid, and the operator got a templating traceback instead of "it has no database
   yet". `regex_findall` degrades cleanly.

### Payload facts, read out of 2.5.0's own server source

- `add(monitor, cb)` requires `accepted_statuscodes` present and all strings.
- `add` writes the monitor row and THEN attaches notifications, so a bad
  `notificationIDList` returns `ok:false` over a monitor that exists. The wiring therefore
  only ever attaches a channel id the live instance still reports, and verifies by reading
  the list back rather than trusting the ack.
- `editMonitor` assigns every column from the payload instead of merging — a partial
  object blanks out everything it does not mention. The wiring lays its fields over the
  full monitor object as Kuma reports it.
- `deleteMonitor(id, deleteChildren, cb)` — `false` is passed explicitly so a group is
  never cascaded into.
- Notification config is **flat**. `Notification.save()` serialises the whole object it is
  handed into the `config` column, so the old REST-shaped payload with a nested `config`
  JSON string would have stored every provider setting one level too deep. The auth method
  literal is `accessToken`; the old code said `token`, which silently sends unauthenticated
  and an Ntfy that ships closed (401) rejects.

### Scope taken beyond the wiring, and why

- **The role's Ntfy channel was dead code.** It sat behind `_kuma_api_usable`, which could
  never be true, so `monitoring.notification_id` was never written and no monitor this
  platform registered could have notified anyone. 303 cannot meet acceptance item 1 with
  it broken, so it moved onto socket.io too.
- **`status.yml` could only ever report zero monitors** — it GETs `/api/monitors`, gets
  HTML, finds no `.json`, and defaults to `[]`. Lab Status (503, marked done, "a fully
  populated report") now reads through the same helpers.
- Removed the REST probe, `_kuma_api_usable`, and the "monitor registration is not yet
  wired" notice. The API key is still minted and stored — it authorises `/metrics` and the
  badges — but nothing in the wiring path needs it, and the deploy notification no longer
  claims registration depends on it.

### Verified live

| Scenario | Result |
|---|---|
| First wire | `changed=1`; monitor row created, `monitor_notification` row attached |
| Re-wire | `changed=0` |
| Drift (url + interval) | `changed=1`; url, interval, retry_interval, timeout all updated, channel still attached |
| Unwire | monitor row gone |
| Unwire again | `changed=0` |
| Host refusing connections | warning, `failed=0`, deploy continues |
| Wrong password | warning naming `authIncorrectCreds`, `failed=0` |
| Kuma still on its setup screen | warning naming the setup screen, `failed=0` |
| Channel provisioning run twice | exactly one channel — the duplicate is gone |

Both gates green. Still unobserved: a real DOWN/UP transition reaching Ntfy — the
rehearsal used a dummy token, so acceptance item 2 needs the lab's real channel.

## 2026-08-09 — acceptance on the lab's own instance

The rehearsal instance proved the mechanism; this proved it against .0.14, which had held
zero monitors since it was deployed on 2026-08-03.

Sequence, driven through the Rundeck API rather than the UI: `Deploy Uptime Kuma` (39),
`Deploy Ntfy` (40), `Deploy Observability` (41). Every check below was made in the
receiving system's own SQLite, never in the job log — the whole point of this slice is
that a green run proves nothing about whether anything arrived.

| Before | After |
|---|---|
| 0 notification channels | 1 — `homelab-infra ntfy`, active, default |
| 0 monitors | 3 — `uptime-kuma`, `ntfy`, `observability`, all bound to channel 1 |
| 0 heartbeats | all three UP, `200 - OK`, through the public HTTPS names |
| `monitoring.notification_id` absent | `'1'` |

**The alert path was untestable until a third service existed.** With only `uptime-kuma`
and `ntfy` registered there is no valid experiment: stopping Ntfy kills the notifier,
stopping Kuma kills the detector, and each would have produced a plausible-looking failure
that proved nothing. `observability` was deployed specifically to be the expendable third
party. This is a property of the estate, not of the code — a two-monitor lab cannot verify
its own alerting.

The transition, with Kuma and Ntfy both healthy throughout:

```
03:04:45  docker stop observability-grafana
03:05:02  Kuma: status 2 (PENDING), "Request failed with status code 502"
03:07:02  Ntfy: "observability Down [Uptime-Kuma]"  prio 5  red_circle
03:07:17  Kuma: status 0, important=1        (cache 47 -> 48)
03:07:40  docker start observability-grafana
03:08:02  Ntfy: "observability Up [Uptime-Kuma]"    prio 5  green_circle
03:08:20  Kuma: status 1, important=1        (cache 48 -> 49)
```

Two things fell out that were not the objective. Kuma held the failure through ~2 minutes
of retries before promoting PENDING to DOWN, so a single blip will not page anyone — the
correct default, previously unobserved. And the deploy notification now reads "signed in as
admin (password in Vaultwarden)" where it used to point at an API key it does not use,
closing the fourth of the four silent failures the REST premise caused.

Grafana was down for about three minutes. Prometheus scraped throughout; nothing was lost.
