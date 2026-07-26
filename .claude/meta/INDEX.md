# Meta Index

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier. Slice template and workflow: [README.md](README.md).

## Status vocabulary

| Status | Means |
|---|---|
| `done` | Acceptance met. Nothing left. Do not reopen without a new slice. |
| `built` | Code written, both gates green, acceptance **not** yet observed on the live lab. |
| `open` | Not started, or started and abandoned mid-way. |

Gates (both current as of 2026-07-26): `wsl bash -lc 'bash .claude/gate/lint.sh'` passes
143 files on the `production` profile; `.claude/gate/test.sh` syntax-checks every playbook
clean. **Both gates are green** — slice 502 closed the last red one.

Counts: **12 done · 27 built · 3 open.**

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

## Built — awaiting live acceptance (26)

Every one of these is code-complete and gate-verified. **All but 504 clear on the same
event: a live bootstrap run against the lab** (slice 500's acceptance); 504 needs a
populated `config/` on the runner instead. Per-slice deviations and open questions live in
each slice's `notes.md`.

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
| 503 | [Lab status](503-lab-status/README.md) | ran green via Rundeck 2026-07-26 but against 0 tagged guests — re-observe once anything is deployed |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | wiring verified live read-only; needs `config/` on a runner for the full play chain + Ntfy |
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | a restore into a fresh Semaphore |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | **15/15 imported live 2026-07-26**; 1 of 15 has run — the 14 that mutate the lab are unobserved |

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

## Open (3)

All three raised by the operator on 2026-07-26, reviewing the Rundeck runner handover. All
are design defects in shipped code, not new features.

| # | Slice | Why now |
|---|---|---|
| 010 | [Config provenance](010-config-provenance/README.md) | The whole one-click platform runs off `config/proxmox.yml`, hand-written on one LXC, unversioned, unbacked-up, root token in plaintext. Nothing creates, validates or can reconstruct it. |
| 011 | [IP allocation model](011-ip-allocation-model/README.md) | `generate-ip.yml` is a flat +1 walk with one global offset. The live lab addresses by function across three bands in a single /20; a flat allocator ignores that and erodes it on every deploy. |
| 012 | [Runner onboarding](012-runner-onboarding/README.md) | The handover is 14 manual steps against a promise of two, one of them (refreshing the runner's checkout) documented nowhere and owned by nothing. No README at the repository root. |

**010 and 012 both gate the shareability claim** and **011 gates the first provisioning
run** — the first deploy that allocates an address bakes in whatever the current model
produces.

## Recommended order

1. **011 before any provisioning job runs.** The moment Deploy Vaultwarden allocates an
   address, the flat model's output is on the wire and in the inventory. Cheaper to fix the
   allocator than to renumber guests.
2. **010 alongside it** — same edit surface (`config/proxmox.yml`, `config.example`,
   CONTRACT.md §2), and it removes the plaintext root token from the runner while the lab
   is still empty enough for a mistake to cost nothing.
3. **012 with 010** — eight of 012's fourteen manual steps are 010's credentials seen from
   the operator's side. 010 decides where a secret lives; 012 decides who puts it there.
   Splitting them means editing `bootstrap-rundeck.sh`, both UI job sets and both READMEs
   twice.
4. **Live bootstrap run** — one event converts 24 of the `built` slices. Still the largest
   single risk-reducer in the backlog, but see the parallel-instance caveat below.
5. **A media app role** — 504 wires the media stack but nothing deploys it. A `sonarr` role
   writing `media.<instance>` on deploy closes the loop; until then media apps join the
   wiring through the `app.media_kind` discovery path.

**Standing caveat on the bootstrap run.** The lab holds 57 LXCs and 4 VMs and **not one
carries the `homelab-infra` tag** — every existing guest was hand-built, so the repo ignores
all of them by design. `bootstrap.yml` will therefore stand up new Vaultwarden, Caddy,
Authentik, Uptime Kuma, Grafana/Prometheus and PBS *beside* the running hand-built ones,
including a second reverse proxy contending for the same domains. That is correct per the
"manages what it creates" philosophy and is not a bug — but it is a deliberate decision to
take, not a surprise to hit mid-run.

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

From the first live Rundeck run (2026-07-26):

- **`with-proxmox-env.sh` now resolves its own Python** (`059316a`). No job step in either
  UI puts the ansible venv on `PATH` — they call `"$VENV/ansible-playbook"` by absolute
  path — so the wrapper's hardcoded `python3` was the distro interpreter, which has no
  PyYAML. **All 15 Rundeck jobs failed identically** at config parse before Ansible was
  reached. It now tries `$PYTHON`, then the `python3` sibling of the ansible command it is
  handed, then `PATH`, taking the first that imports yaml. Fixed in the one wrapper rather
  than in 15 job files; Semaphore's steps share the wrapper and inherit the fix.
- **`rd` is not required.** The Rundeck REST API accepts the same job YAML the CLI sends
  (`POST /api/47/project/<p>/jobs/import`, `Content-Type: application/yaml`). Nothing in
  the repo depends on the CLI being installed; the README's `rd` loop remains one valid path.
- **The runner is a documented host now, not a mystery.** LXC 13228 `pve-rundeck-4` on
  pve-host-3, project `homelab-infra`, checkout at `/var/lib/rundeck/homelab-infra` tracking
  `origin/master`, venv at `/opt/homelab-ansible`, Proxmox token `root@pam!rundeck`
  (privsep off). Slice 010 exists because that host's `config/` is the platform's only copy
  of its own credentials.
- **`ansible/.ansible/` is gitignored** — ansible-lint's local cache was staging itself into
  commits.

From the 504 build (2026-07-25):

- **New registry key `media`** (CONTRACT.md §3) — instance-keyed, not role-keyed, because a
  lab runs several Sonarrs. Read only by `wire-media-stack.yml`.
- **Three optional `app:` keys** in instance files: `media_kind`, `host`, `api_key`. Their
  presence is what enrols an app in media wiring, so a lab can wire apps it did not deploy.
- **New shared directory** `tasks/app-wiring/` and its table `vars/media-wiring.yml` —
  cloning the playbook for another stack means changing which task files it loops over.

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
  their parameters. 504 added `Wire Media Stack` to both UIs, parameter-free.
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
