# 501 — notes

## 2026-07-25 — implementation

`playbooks/apps/remove.yml` implemented as three plays. Both gates green.

### Deviations from the README approach

**Unwire runs before the app is stopped, in the same play that locates it.** The README
listed unwire first, the playbook's old header comment listed it third. Unwire-first is
correct and is now the documented order: traffic is drained at the proxy and the uptime
monitor is deleted *before* the backend stops answering, so removing an app does not
page the operator with a DOWN alert for something they removed on purpose. The unwire
tasks need only `wiring_app_name` + `wiring_domain`, never an upstream host or port,
which is exactly what lets them run before (and independently of) the host lookup.

**Docker vs native is decided by `app_config.stack`, not `proxmox.type`.** The README
offered either. `stack` is the key the deploy playbooks themselves branch on
(`find-or-create-host.yml` vs the native LXC path), so removal branches on the same
thing and cannot disagree with the deploy.

**New optional `app` parameter.** Removal needs `vars/app-defaults/<app>.yml` to know
the app's shape, and the instance name is not always the app name (`radarr-4k`). It
defaults to `instance` — one-click for the normal case — and the assert names the flag
when nothing describes the instance.

**Authentik unwire is unconditional on `routing.identity`.** The wire half dispatches
per mode; the unwire half already tolerates every shape being absent, so running it
regardless also cleans up after an instance whose identity mode changed between deploys.
Gated on `sso.token` being present, since the task asserts on it.

**Native service name comes from `app.service_name`.** A unit name is not always the app
name (PBS runs `proxmox-backup-proxy`). Added `app.service_name` to the four native
baseline app-defaults (vaultwarden, ntfy, caddy, pbs) and to
`vars/app-defaults/_template.yml`, defaulting to the instance name when unset. This is
additive to slices 400/401/402/406 — no behaviour change to their deploys.

**A missing host is a report, not a failure.** If the guest is already gone, Play 1 adds
nothing to the removal group, Play 2's host pattern matches nothing and the play is
skipped. That is what makes a second `remove` run a clean no-op (acceptance item 3).

### Deliberately not done

Neither the stack host nor a native app's own LXC is destroyed, per the README. For a
native app this means removal leaves an empty guest behind — deliberate: re-running the
deploy converges that same guest back, which is the restore story
`config/apps/<instance>.yml` exists for.

### What live acceptance must confirm

- All four unwire halves against real Caddy / Authentik / Kuma / OPNsense endpoints
  (they have never run live — slices 300, 302, 303, 304 are all awaiting the same event).
- `docker_compose_v2 state: absent` with `remove_volumes: false` leaves the volume behind
  and a re-deploy finds its data.
- The `'could not be found'` message match for an absent systemd unit — that string is
  the module's, and it is the one assumption here that a live run could falsify.

## 2026-08-02 — live acceptance run (partial)

`Remove App` was driven from the Rundeck API against the whole live baseline as the
first step of a deliberate teardown. Executions 13–21 on the runner. **Four of the five
acceptance items are met; the fifth is false.**

Met: Docker app removal (observability, uptime-kuma, authentik — Compose down, Caddy
route deleted, Authentik application deleted), native LXC removal (ntfy, vaultwarden,
caddy — unit stopped and disabled, data path deleted under `delete_data=true`),
`config/apps/<instance>.yml` survived, and the Ntfy notification fired while Ntfy was
still up. `docker_compose_v2 state: absent` and the `'could not be found'` systemd
message match both behaved as assumed — no surprises in Play 2.

### Removal is only idempotent while every platform provider is still up

Acceptance item 3 ("re-running remove on an already-removed app is idempotent") holds
only in the narrow case the note above imagined: the guest is gone but Caddy, Authentik
and Kuma are all still answering. Play 1 runs *before* the host lookup, so a re-run
reaches the unwire tasks unconditionally, and two of the four abort the playbook when
their provider is unreachable:

- **`tasks/unwiring/caddy.yml`, `Check for existing route`** — `uri` with
  `status_code: [200, 404]` fails on a connection error (`status: -1`), so removal dies
  before Play 2. Execution 21: re-running `remove vaultwarden` after Caddy was removed
  failed with `Connection refused` on `http://192.168.0.12:2019/id/route_vaultwarden`.
- **`tasks/unwiring/authentik.yml`, `Find proxy provider`** — same shape, and worse to
  diagnose because the task carries `no_log: true`: the operator sees only
  `Result code was 2`. Executions 16 and 17 (`pbs`, `ntfy`) both died here once
  Authentik had been removed.

`unwiring/uptime-kuma.yml` is the one that gets this right — it probes with
`failed_when: false` and degrades to a `debug` that names the stale monitor and tells
the operator to delete it by hand. That probe-first stance is the pattern the Caddy and
Authentik unwire halves need; the header comment in `unwiring/uptime-kuma.yml` already
argues for it in general terms ("a stale monitor is a smaller problem than a removal
that aborts halfway") but only that one file implements it.

This is not a teardown-only edge case. Any app removed while its reverse proxy is down
for maintenance hits it, and the failure mode is the one the unwire-first ordering
exists to prevent: the playbook aborts *between* unwiring and stopping the app.

### Teardown has no ordering contract

Removing the baseline in an arbitrary order strands the rest. `authentik` was removed
third, which broke `pbs` and `ntfy` immediately; the run was only unblocked by hand-
editing `sso.provider: none` into `config/.generated/facts.yml`. Nothing in the job,
the playbook header or the docs says removal must run in reverse bootstrap order
(PBS → observability → Uptime Kuma → Authentik → proxy → Ntfy → Vaultwarden), and a
one-click UI offers no way to express that dependency. Once the two unwire halves above
degrade gracefully, order stops mattering — which is the better fix than documenting an
order the operator has to remember.

### Uptime Kuma's probe accepts the SPA as a working REST API

`GET /api/monitors` on Uptime Kuma 2.x returns **200 with `text/html`** — the Vue SPA's
index page, served as the catch-all for unknown routes. The probe only checks
`status == 200`, so it concludes the REST API is usable, `_kuma_probe.json` is absent,
the monitor selection falls through to `{}`, the delete is skipped, and
`Assert monitor removed` passes **vacuously** against another page of HTML. Every
removal therefore reported the monitor gone without ever having looked for one.

Slice 404 already records that Kuma has no REST write API. What is new here is that the
*unwire* half reports success rather than reaching its own "Report unusable API" branch.
Whatever replaces this (socket.io, per 404) must not treat a 200 as proof of an API —
the check needs to be the content type or the shape of the body.

### The removed baseline

observability, uptime-kuma, authentik (Docker); ntfy, vaultwarden, caddy (native LXC).
`pbs` was never removed — execution 16 died at the Authentik unwire before reaching it,
and by then the teardown had its finding. Guests were destroyed manually afterwards, as
designed: removal never destroys a guest.
