# Meta Index

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier. Slice template and workflow: [README.md](README.md).

## Status vocabulary

| Status | Means |
|---|---|
| `done` | Acceptance met. Nothing left. Do not reopen without a new slice. |
| `built` | Code written, both gates green, acceptance **not** yet observed on the live lab. |
| `open` | Not started, or started and abandoned mid-way. |

Gates (both current as of 2026-07-25): `wsl bash -lc 'bash .claude/gate/lint.sh'` passes
137 files on the `production` profile; `.claude/gate/test.sh` syntax-checks every playbook
clean. **Both gates are green** — slice 502 closed the last red one.

Counts: **12 done · 26 built · 1 open.**

---

## Done (12)

No further work. Listed for provenance only.

| # | Slice |
|---|---|
| 000 | [Variable-loading contract (spec)](000-variable-loading-contract/README.md) |
| 001 | [Implement config/*.yml loader](001-config-loader/README.md) |
| 002 | [Reconcile config.example schema](002-reconcile-config-example/README.md) |
| 003 | [Filter proxmox module params](003-filter-proxmox-module-params/README.md) |
| 004 | [Proxmox key naming unification](004-proxmox-key-naming/README.md) |
| 005 | [Instance config schema contradiction](005-instance-config-schema/README.md) |
| 006 | [generate-ip combine](006-generate-ip-combine/README.md) |
| 007 | [requirements.yml collections](007-requirements-collections/README.md) |
| 100 | [unattended-upgrades dedupe](100-unattended-upgrades-dedupe/README.md) |
| 101 | [Stack key guard in template](101-stack-key-guard/README.md) |
| 102 | [Restart/tail assert ordering](102-restart-tail-assert-order/README.md) |
| 103 | [find-or-create-host docs](103-find-or-create-host-docs/README.md) |
| 200 | [write-generated-facts](200-write-generated-facts/README.md) |

## Built — awaiting live acceptance (25)

Every one of these is code-complete and gate-verified. **They all clear on the same
event: a live bootstrap run against the lab** (slice 500's acceptance). Per-slice
deviations and open questions live in each slice's `notes.md`.

| # | Slice | What live acceptance needs |
|---|---|---|
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | a second-domain deploy |
| 009 | [Identity-mode contract (routing.identity)](009-identity-modes/README.md) | one app deployed per mode |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | Ntfy running (401) |
| 202 | [configure-pbs](202-configure-pbs/README.md) | PBS running (406) |
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | Caddy running (402) |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | an nginx lab — none exists; see below |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | Authentik running (403) |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | Kuma running (404) |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | OPNsense API creds |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | a Pihole — user runs OPNsense; low priority |
| 306 | [Reverse-proxy forward_auth](306-wiring-forward-auth/README.md) | Caddy path verified live 2026-07-25; browser sign-in leg + nginx path open — see below |
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | bootstrap step 1 |
| 401 | [Ntfy](401-app-ntfy/README.md) | bootstrap step 2 |
| 402 | [Caddy](402-app-caddy/README.md) | bootstrap step 3 |
| 403 | [Authentik](403-app-authentik/README.md) | bootstrap step 4 — one item blocked on 306 |
| 404 | [Uptime Kuma](404-app-uptime-kuma/README.md) | bootstrap step 5 |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | bootstrap step 6 |
| 406 | [PBS](406-app-pbs/README.md) | bootstrap step 7 — VM path never run live |
| 407 | [Caddy per-estate DNS-01](407-caddy-dns-challenge/README.md) | a real public domain + DNS token |
| 500 | [Bootstrap plays](500-bootstrap-plays/README.md) | the full run — the event above |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | remove a deployed app; needs 300/302/303/304 live |
| 502 | [Rollback container](502-rollback-container/README.md) | roll a Docker app back a tag |
| 503 | [Lab status](503-lab-status/README.md) | one run against a populated lab |
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | a restore into a fresh Semaphore |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | `rd jobs load` of all 14 files |

Carried caveats:

- **301/305 have no live target.** The lab runs Caddy + OPNsense. These two stay `built`
  indefinitely unless a second lab appears; that is expected, not a stall.
- **406's VM provisioning machinery** (`tasks/proxmox/ensure-cloud-template.yml`,
  `vm-clone.yml`) has never executed. Highest live-run risk in the set.
- **403 acceptance item 3** — 306 landed and the Caddy enforcement is verified live, so
  the fail-open gap is closed. The item still needs an app actually deployed with
  `routing.identity: forward_auth` to be observed end to end.
- **306's two remaining items** are the interactive browser sign-in leg (the redirect to
  the flow is verified; completing it needs a human at a browser) and the nginx path,
  which shares 301's no-NPM-lab carve-out.
- **500's one staged import is `apps/nginx.yml`**, which does not exist (301 shipped the
  wiring pair only, no app playbook).
- **600's backup schema is reconstructed, not exported** from a running Semaphore. If the
  restore rejects it, dump `GET /api/project/<id>/backup` and commit the server's output.

## Open (1)

| # | Slice | Depends on | Ready? |
|---|---|---|---|
| 504 | [Wire media stack](504-wire-media-stack/README.md) | 300–305 | blocked in practice — no media app exists to wire |

## Recommended order

1. **Live bootstrap run** — one event converts 24 of the 25 `built` slices. The backlog of
   unverified work is now the project's *only* significant risk, and nothing else in the
   backlog reduces it.
2. **Import one UI** (600 or 601) and drive the live run from it, so the job definitions
   are verified by the same event rather than in a second pass.
3. **504** — deferred until a media stack app actually exists to wire. Adding a media app
   is the prerequisite, not more scaffolding.

## Retired trackers

`.claude/meta/` is the single backlog. Two earlier systems overlapped it and are being wound down:

- **`.claude/plans/`** — deleted 2026-07-25. Its six `design/` forms (dhcp lease discovery,
  check-native-updates report play, stack-host docker readiness, docker apt keyring, default LXC
  password, secrets in guest JSON) were all verified implemented in the tree by later tier work,
  and its two `concept/` notes were absorbed: the red-test-gate note's two `hosts:` defects by
  slice 102, its third by slice 502; the gate-wrapper note into `.claude/gate/README.md`.
- **`.claude/isotope-intake-backlog.md` + `.isotope/cultures/flux/`** — an abandoned migration of
  `meta/` + `plans/backlog/` into Isotope specimens. All eight flux specimens describe work that
  has since landed, and `.isotope/isotope.json` points at a checkout path that does not exist.
  Not yet removed — decide before it accrues more stale state.

## Cross-slice effects on record

From the 5XX/6XX build (2026-07-25):

- **`app.service_name` added** to the four native baseline app-defaults (vaultwarden,
  ntfy, caddy, pbs) and `vars/app-defaults/_template.yml`. 501 stops a native app by its
  unit name, which is not always the app name (PBS runs `proxmox-backup-proxy`).
  Additive — no deploy behaviour changed.
- **`scripts/with-proxmox-env.sh` accepts `config/proxmox.yml`** (top-level `proxmox:`)
  as well as the legacy `homelabinfra_config:`-wrapped user-vars file. Every Rundeck job
  step depends on this; the CLI path in both READMEs now points at the config file rather
  than a legacy vars file.
- **Both UIs ship one job per app**, with `instance=<app>` baked in — no survey to fill
  for a deploy. `Remove App`, `Restart App`, `Tail App Log` and `Rollback Container` keep
  their parameters. Neither UI defines a Wire Stack job (504's playbook does not exist).
- **`test.sh` is green for the first time** — 502 replaced the stub that was failing it.

From the 4XX build (2026-07-25):

- **401 closed Ntfy by default** and reconciled all five notification consumers to
  authenticate. `notifications` gained optional `user`/`password`/`token` in
  `ansible/vars/CONTRACT.md` §3; consumers fall back to anonymous POST when no token is
  recorded, so `git pull` does not break an existing lab.
- **303 renamed the registry key** `uptime_kuma` → `monitoring` (CONTRACT.md §3); 404
  writes it. **404 locked Uptime Kuma v2**, resolving 303's open question toward its
  existing REST implementation — no rework.
- **405 added `prometheus-node-exporter` to `tasks/guest-bootstrap.yml`** — every guest is
  scrapeable, not just the observability host.
- **New shared task** `tasks/notify.yml`; **new registry key** `metrics`.
- **Fact-writing moved into the app playbooks.** Each baseline app records its own registry
  key in Play 3 before wiring, so a standalone deploy registers the service identically to
  a bootstrap run. `bootstrap.yml` no longer writes facts on their behalf.
- **009 reworked 302**: `tasks/wiring/authentik.yml` dispatches on `wiring_identity_mode` —
  catalog (Application tile only), oidc (OAuth2 provider, client creds returned to the
  caller), forward_auth (the original proxy-provider path, unchanged); unwire removes
  whichever shape exists.
- **403 gained a `routing.subdomain` default of `auth`** so multi-estate labs reach each
  estate's Authentik at its own `auth.<domain>`. Directory content — accounts, groups,
  social sources, MFA — is out of the role's scope; see `roles/authentik/README.md`.
