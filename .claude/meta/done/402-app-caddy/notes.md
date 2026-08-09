# 402 — notes

## 2026-07-25 — implementation

Implementation complete and gate-verified; slice stays in-progress until a live
deploy confirms the acceptance items.

### What's in

- `roles/caddy/` — official Cloudsmith apt repository, JSON base config, systemd
  drop-in, admin-API reconcile, three `lab-*` scripts, `facts.d` version record.
- `playbooks/apps/caddy.yml` — PATH B native LXC; records `reverse_proxy` in Play 3
  before wiring.
- `vars/app-defaults/caddy.yml`, `config.example/apps/caddy.example.yml`.
- `playbooks/bootstrap.yml` step 3 activated (caddy only — see below).

### Decision: JSON config, not a Caddyfile

Slice 300 assumes routes are POSTed to
`/config/apps/http/servers/srv0/routes`. An **empty Caddyfile adapts to a config
with no http servers at all**, so `srv0` would not exist and the very first app
wire would 404. A JSON base config guarantees `srv0` exists with an empty route
table. `/etc/caddy/Caddyfile` is removed so the packaged unit cannot silently apply
an empty server set over it.

### Decision: `--resume`, and restart-never-reload

The systemd drop-in runs `caddy run --config /etc/caddy/caddy.json --resume`. On
first start there is no autosave so the base config applies; afterwards Caddy
replays its autosaved running config, so admin-API routes survive restarts and
reboots. Consequences, both deliberate:

- The role's handler **restarts**; it never reloads. `caddy reload` re-applies the
  base file and would drop every wired route.
- `lab-restart-app` restarts for the same reason, and says so.
- `systemctl reload caddy` is a route-wiping operation. ExecReload is overridden to
  point at the JSON config so at least it is coherent, and the behaviour is
  documented in the role header.

### Decision: reconcile two subtrees, not the whole config

Per CLAUDE.md ("fire-and-forget provisioning — we do not police drift") the role
does not rewrite a healthy running config. It POSTs `/load` only when no `srv0`
exists (fresh install, or an autosave that lost it — safe precisely because there
are no routes to lose). Otherwise it PATCHes only the two subtrees it owns, srv0
`listen` and the TLS automation policies, so routes are never touched.

### Addition beyond the README: `tls_mode`

The README asked to pick HTTP-01 vs DNS-01 and defer DNS-01. Shipped instead:
`app.tls_mode: acme | internal`. `internal` uses Caddy's built-in CA, which solves
the internal-only lab properly — no public DNS, no port 80 exposure, no ACME at all —
where DNS-01 would only move the problem to per-provider credentials. `acme` remains
the default. DNS-01 stays future work.

### Contract deviation from this slice's README

The README sketched writing `caddy.admin_api_url` to facts. That is the superseded
service-keyed shape (CONTRACT.md §3 "Superseded (b)"), and slice 300's notes already
called this out. Implementation writes Shape B
`reverse_proxy: {provider, instance, host, port}` with `host` carrying the scheme
and `port` = 2019 (the **admin API** port, which is what `wiring/caddy.yml` builds
its base URL from). The public 80/443 listeners are not recorded — nothing reads them.

### Other

- `app.port` is the admin API port, because that is what the wiring contract and the
  uptime monitor consume. `http_port`/`https_port` are separate keys.
- `routing.proxy: none` — the reverse proxy must not route itself; the playbook's
  reverse-proxy wiring step is gated on it.
- The uptime monitor targets the admin API over the LAN: Caddy answers 404 on :80 for
  a host it has no route for, so there is no other reliably-200 endpoint.
- apt install uses `state: latest` with a `noqa package-latest`, because re-running
  the deploy IS the documented update mechanism for native apps.
- Bootstrap's nginx import stays commented: slice 301 shipped nginx *wiring*, but
  there is no `apps/nginx.yml` yet, and `import_playbook` is parsed at load time so
  importing a missing file breaks `--syntax-check` for all of bootstrap.yml.

### Verification

- ansible-lint: clean (production profile).
- syntax-check: `playbooks/apps/caddy.yml` and `playbooks/bootstrap.yml` clean.
  Repo-wide, only the pre-existing slice-502 stub fails.
- NOT verified live.

### Live acceptance TODO

- Admin API reachable from the controller; `curl http://<caddy-ip>:2019/config/`
  returns the base config with an empty `srv0.routes`.
- First wired app gets a working certificate in both `tls_mode` settings.
- Re-run is a no-op and does not drop routes.
- Restart the LXC: routes survive (this is the `--resume` assumption, and the single
  most important thing to verify live).
