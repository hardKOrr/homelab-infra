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
