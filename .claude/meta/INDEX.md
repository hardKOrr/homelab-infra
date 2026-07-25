# Meta Index

Numbering scheme: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two digits are order within the tier. See [README.md](README.md) for slice template and workflow.

## 0XX — Foundation (architecture/variable plumbing; nothing else runs until these work)

| # | Slice | Status | Depends on | Blocks |
|---|---|---|---|---|
| 000 | [Variable-loading contract (spec)](000-variable-loading-contract/README.md) | done | none | 001–004 and everything downstream |
| 001 | [Implement config/*.yml loader](001-config-loader/README.md) | done | 000 | 004, 200, every playbook importing load-user-vars.yml |
| 002 | [Reconcile config.example schema](002-reconcile-config-example/README.md) | done | 000, 001 | 004; any user attempting the documented workflow |
| 003 | [Filter proxmox module params](003-filter-proxmox-module-params/README.md) | done | 000 | any real LXC/VM provisioning |
| 004 | [Proxmox key naming unification](004-proxmox-key-naming/README.md) | done | 002 | all proxmox-touching work |
| 005 | [Instance config schema contradiction](005-instance-config-schema/README.md) | done | none | any real app deploy |
| 006 | [generate-ip combine](006-generate-ip-combine/README.md) | done | none | safe reuse of generate-ip |
| 007 | [requirements.yml collections](007-requirements-collections/README.md) | done | none | any docker app, guest-bootstrap |
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | in-progress² | 000, 001, 200 | any second-domain deployment; 009, 407 |
| 009 | [Identity-mode contract (routing.identity)](009-identity-modes/README.md) | in-progress² | 008, 302, 403 | 306 |

## 1XX — Hygiene (small fixes, no architectural impact)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 100 | [unattended-upgrades dedupe](100-unattended-upgrades-dedupe/README.md) | done | none |
| 101 | [Stack key guard in template](101-stack-key-guard/README.md) | done | 005 |
| 102 | [Restart/tail assert ordering](102-restart-tail-assert-order/README.md) | done | none |
| 103 | [find-or-create-host docs](103-find-or-create-host-docs/README.md) | done | 006 |

## 2XX — Bootstrap helpers (tasks/bootstrap/* building blocks)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 200 | [write-generated-facts](200-write-generated-facts/README.md) | done | 004 |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | in-progress¹ | 200, 401 |
| 202 | [configure-pbs](202-configure-pbs/README.md) | in-progress¹ | 200, 406 |

¹ Implementation complete and gate-verified; live acceptance blocked on the app slice it depends on.

## 3XX — Wiring (per-provider wire/unwire pairs)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | in-progress¹ | 200 |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | in-progress¹ | 200 |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | in-progress¹ | 200, 403 |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | in-progress¹ | 200, 404 |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | in-progress¹ | 200 |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | in-progress¹ | 200 |
| 306 | [Reverse-proxy forward_auth for forward-auth-mode apps](306-wiring-forward-auth/README.md) | open | 300, 301, 302, 403, 009 |

¹ Implementation complete and gate-verified; live acceptance needs the provider running.
Per-slice decisions and deviations are in each slice's `notes.md` — 303 additionally
renamed the registry key `uptime_kuma` → `monitoring` (CONTRACT.md §3), which slice 404
now writes.

302 was reworked by slice 009: `tasks/wiring/authentik.yml` now dispatches on
`wiring_identity_mode` — catalog (Application tile only), oidc (OAuth2 provider,
client creds exported to the caller), forward_auth (the original proxy-provider
path, unchanged) — and the unwire removes whichever shape exists.

306 was opened while implementing 403: the wire tasks create every Authentik object
but emit no `forward_auth` handler, so forward-auth fails **open** and silently.
Since slice 009 it blocks only the `forward_auth` exception mode (no baseline app
defaults to it); catalog/oidc modes never promised proxy enforcement.

## 4XX — Apps (per-app roles + per-app playbooks)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | in-progress² | 004, 005, 200 (+000–003 foundation) |
| 401 | [Ntfy](401-app-ntfy/README.md) | in-progress² | 200, 400 |
| 402 | [Caddy](402-app-caddy/README.md) | in-progress² | 300, 401 |
| 403 | [Authentik](403-app-authentik/README.md) | in-progress²ᐟ³ | 302, 401 |
| 404 | [Uptime Kuma](404-app-uptime-kuma/README.md) | in-progress² | 303, 401 |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | in-progress² | 401 |
| 406 | [PBS](406-app-pbs/README.md) | in-progress² | 202, 401 |
| 407 | [Caddy per-estate DNS-01 challenge](407-caddy-dns-challenge/README.md) | in-progress² | 402, 008 |

² Implementation complete and gate-verified; awaiting live deploy acceptance (see slice notes.md).

³ 403 additionally has one acceptance item blocked on work it does not own: no
wiring task emits a reverse-proxy `forward_auth` handler, so nothing enforces the
`forward_auth` identity mode. Owned by slice 306 — see 403's notes.md "Known gap".
403 gained a `routing.subdomain` default of `auth` (2026-07-25) so multi-estate
labs reach each estate's Authentik at its own `auth.<domain>`; it is a default,
overridable per instance like any routing key. Directory content — account names,
groups, social login sources, MFA — is explicitly out of the role's scope; see
`roles/authentik/README.md`.

Cross-slice effects of the 4XX build (2026-07-25):
- **401 closed Ntfy by default** and reconciled all five existing notification
  consumers to authenticate. `notifications` gained optional `user`/`password`/`token`
  in `ansible/vars/CONTRACT.md` §3; consumers fall back to anonymous POST when no
  token is recorded, so an existing lab is not broken by `git pull`.
- **404 locked Uptime Kuma v2**, which resolves slice 303's open question in the
  direction that needs no rework — 303's REST implementation stands.
- **405 added `prometheus-node-exporter` to `tasks/guest-bootstrap.yml`**, so every
  guest is scrapeable. This touches all guests, not just the observability host.
- **406 added VM provisioning machinery** (`tasks/proxmox/ensure-cloud-template.yml`,
  `tasks/proxmox/vm-clone.yml`) reusable by any future VM app. Never run live.
- **New shared task** `tasks/notify.yml`; new Shape B registry key `metrics`.
- **Fact-writing moved into the app playbooks.** Each baseline app records its own
  registry key in its Play 3 before wiring, so a standalone deploy registers the
  service identically to a bootstrap run. `bootstrap.yml` no longer writes facts on
  their behalf (its Vaultwarden facts play moved into `apps/vaultwarden.yml`).

## 5XX — Top-level playbooks

| # | Slice | Status | Depends on |
|---|---|---|---|
| 500 | [Bootstrap plays](500-bootstrap-plays/README.md) | in-progress⁴ | 400–406 |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | open | 300–305 (unwire halves) |
| 502 | [Rollback container](502-rollback-container/README.md) | open | 201 |
| 503 | [Lab status](503-lab-status/README.md) | open | none |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | open | 300–305 |

⁴ Structure, two-pass Vaultwarden token gate, and **all seven steps** implemented and
gate-verified — 401–406 landed and their imports are active. The one remaining staged
import is `apps/nginx.yml` (no nginx app playbook exists; slice 301 shipped only the
nginx wiring pair). Fact-writing now lives in each app playbook rather than here.
Live acceptance needs a full bootstrap run.

## 6XX — UI (Semaphore + Rundeck job definitions)

| # | Slice | Status | Depends on |
|---|---|---|---|
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | open | 500 |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | open | 500 |
