# Meta Index

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier. Slice template and workflow: [README.md](README.md).

## Status vocabulary

| Status | Means |
|---|---|
| `done` | Acceptance met. Nothing left. Do not reopen without a new slice. |
| `built` | Code written, both gates green, acceptance **not** yet observed on the live lab. |
| `open` | Not started, started and abandoned, or reopened by evidence. |

Gates: `wsl bash -lc 'bash .claude/gate/lint.sh'` (ansible-lint on the `production`
profile plus `jinja-parse.py`, which compiles every Jinja expression in `ansible/`) and
`.claude/gate/test.sh` (`--syntax-check` over every playbook). **Both green.**

Counts: **20 done · 23 built · 4 open.**

## Where the platform stands

A second estate — separate from the hand-built lab — runs seven guests on pve-host-3,
all tagged `homelab-infra`, all built by this repo:

| | |
|---|---|
| **Converges** | `Bootstrap Platform` re-runs to `changed=0` on every host (execution 34, 2026-08-08). That was the last open item on 500 and it took eight defects to reach. |
| **Serves HTTPS** | One `*.wasitacatisaw.cc` Let's Encrypt certificate via Cloudflare DNS-01 covers all six estate hostnames; every one verifies. |
| **Keeps its secrets in the vault** | Vault mode, `facts.yml` secret-free, the automation account drives every write, and the fail-closed guarantee has been tested by injection. |
| **Backs itself up** | PBS holds 30 snapshots — five consecutive nights for each of six guests, unattended. |
| **Is monitored, partly** | Prometheus scrapes all seven guests. **Uptime Kuma has never initialized** — see below. |

**The one thing that is not working is Uptime Kuma**, and it is the sharpest lesson
available right now: it has been deployed, healthy and green since 2026-08-03 while
sitting on its "choose a database" setup screen, with no admin user, no monitors and no
database. Four green bootstrap runs passed over it. Nothing in the deploy asserts that an
application is *usable* — only that its container is up — so an app waiting for a human
is indistinguishable from a working one. Slice **404** is reopened on that basis.

## Standing lessons

These are the ones that have earned their place by recurring. Per-session narrative lives
in the slice's own `notes.md`, not here.

**State survives boundaries the code assumes are fresh.** Four instances now:
`homelabinfra_config` carried across plays; a cloud template carried across runs; an
artifact deleted by one step that the next still expected; and a bind-mounted config file
whose inode the container kept after Ansible replaced it. Suspect it first on any "already
exists", "missing file", or "my change had no effect" symptom.

**A guard can work while the path around it does not.** Break-glass recovery was
unreachable in vault mode twice — `config-doctor` and then `with-proxmox-env.sh` each
demanded a token that recovery exists precisely to do without. The gates cannot see this
class of defect; only injection testing finds it.

**Green is not working.** The gates are lint and syntax; they say nothing about whether
the platform runs. The first from-scratch runner bootstrap hit fifteen blockers with both
gates green, eleven of them in the seam between the repo and the machine that runs it.
Uptime Kuma is the same lesson one level up: the run was green, the app was not started.
Worth adding to the gates: a smoke target that provisions one throwaway guest,
`shellcheck` over `rundeck/*.sh` + `ansible/scripts/*.sh`, and a file-mode assertion.

**Convergence was the least-verified property in the repo**, despite being the one the
day-2 model leans on hardest ("re-running a deploy IS the update mechanism"). It was
invisible to per-app testing, which only ever runs a service once successfully. Reaching
`changed=0` on 2026-08-08 cost eight defects, two of them real data risks: the vault
upsert deleted a field a later write in the same run restored, and Prometheus could not
receive a config change at all.

**Some acceptance criteria describe a design that has since been replaced**, so they can
never be ticked as literally written and the slice looks permanently open. 401, 402 and
404 all name `facts.yml` keys that slices 200 and 014 moved or deleted; 402 names HTTP-01
where 015 landed a DNS-01 wildcard. Where the shipped mechanism satisfies the *intent*,
record it met against the shipped shape and say so — do not leave it unticked.

## Done (20)

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
| 017 | [routing.access split](017-routing-access-split/README.md) |
| 100 | [unattended-upgrades dedupe](100-unattended-upgrades-dedupe/README.md) |
| 101 | [Stack key guard in template](101-stack-key-guard/README.md) |
| 102 | [Restart/tail assert ordering](102-restart-tail-assert-order/README.md) |
| 103 | [find-or-create-host docs](103-find-or-create-host-docs/README.md) |
| 200 | [write-generated-facts](200-write-generated-facts/README.md) |
| 202 | [configure-pbs](202-configure-pbs/README.md) — 2026-08-08; the nightly job had been completing all along, and excluded PBS from backing itself up |
| 401 | [Ntfy](401-app-ntfy/README.md) — 2026-08-08; closed-by-default policy verified through the public HTTPS origin |
| 402 | [Caddy](402-app-caddy/README.md) — 2026-08-08; wildcard TLS and the live route set |
| 406 | [PBS](406-app-pbs/README.md) — 2026-08-03; the VM provisioning machinery ran for the first time |
| 500 | [Bootstrap plays](500-bootstrap-plays/README.md) — 2026-08-08; `changed=0` on every host |
| 503 | [Lab status](503-lab-status/README.md) — 2026-08-08; a fully populated report |

## Built — awaiting live acceptance (23)

Code-complete and gate-verified. Each row keeps its unobserved external, browser,
credential, or mutation leg explicit.

| # | Slice | What live acceptance needs |
|---|---|---|
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | a second-domain deploy |
| 009 | [Identity-mode contract (routing.identity)](009-identity-modes/README.md) | one app deployed per mode |
| 010 | [Config provenance](010-config-provenance/README.md) | the bootstrap script run on a node. The Config job group is verified on the workstation; nothing the script does is |
| 012 | [Runner onboarding](012-runner-onboarding/README.md) | Configure App / Get Config from the UI, `LAB_REFRESH=0`, a no-op re-run of the bootstrap script, and the root README |
| 013 | [Vaultwarden admin token self-capture](013-vaultwarden-token-capture/README.md) | sink write/readback proved live; HTTPS, vault identities and item CRUD belong to 015/016/014 |
| 014 | [Vaultwarden as the generated-secret store](014-vaultwarden-secret-store/README.md) | only the runner rebuild from Key Storage. Seed-file recreation and seed re-entry were both injected and refused, 2026-08-06 |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | a container update actually reported |
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | wiring runs every bootstrap; unwire needs a removal run |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | an nginx lab — none exists; see below |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | second-deploy lookup fixed; browser sign-in leg open |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | blocked on 404 — there is no initialized Kuma to wire into |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | OPNsense API creds |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | a Pihole — user runs OPNsense; low priority |
| 306 | [Reverse-proxy forward_auth](306-wiring-forward-auth/README.md) | Caddy path verified live 2026-07-25; browser sign-in leg + nginx path open |
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | serving, converging and driving every vault write; browser vault CRUD unobserved |
| 403 | [Authentik](403-app-authentik/README.md) | one app deployed with `routing.identity: forward_auth`, observed end to end |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | five of six observed 2026-08-08; only admin sign-in remains |
| 407 | [Caddy per-estate DNS-01](407-caddy-dns-challenge/README.md) | running live on the real domain; a second estate would close it |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | a removal run against a stopped Caddy or Authentik, to confirm the degradation fix |
| 502 | [Rollback container](502-rollback-container/README.md) | roll a Docker app back a tag |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | wiring verified live read-only; needs the full play chain + Ntfy |
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | a restore into a fresh Semaphore |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | 22/22 imported and driven by API; Bootstrap Platform, Lab Status, Remove App, Vaultwarden Cutover / Enrollment / Recovery have all run. The per-app Deploy jobs, Restart App, Tail App Log, Rollback Container, Check Native App Updates, Wire Media Stack and the whole Config group have not |

Carried caveats:

- **301/305 have no live target.** The lab runs Caddy + OPNsense. These two stay `built`
  indefinitely unless a second lab appears; that is expected, not a stall.
- **500's one staged import is `apps/nginx.yml`**, which does not exist (301 shipped the
  wiring pair only, no app playbook).
- **600's backup schema is reconstructed, not exported** from a running Semaphore. If the
  restore rejects it, dump `GET /api/project/<id>/backup` and commit the server's output.
- **010/012's bootstrap script has never run against a real node.** `pveum` role creation,
  config authoring, project creation, Key Storage staging and job import are all
  unexercised. Treat the first run as an experiment.

## Open (4)

All four are design defects in shipped code or gaps between the documented model and the
implemented one. None are new features.

| # | Slice | Why now |
|---|---|---|
| 404 | [Uptime Kuma](404-app-uptime-kuma/README.md) | **Reopened 2026-08-08.** The app has never initialized — Kuma 2 asks for a database backend before anything else and the role does not answer. No admin user, no monitors, no API key. The missing step turns out to be drivable over plain HTTP (`POST /setup-database {"dbConfig":{"type":"sqlite"}}` → `{"ok":true}`), so this is closable rather than blocked. It also blocks 303. |
| 011 | [IP allocation model](011-ip-allocation-model/README.md) | `generate-ip.yml` is a flat +1 walk with one global offset. The live lab addresses by function across three bands in a single /20. **Six addresses are already allocated under the flat model** (.10–.15), so the unwind cost is no longer hypothetical. |
| 015 | [Caddy-first wildcard HTTPS bootstrap](015-wildcard-dns-default/README.md) | Certificate model fixed and observed live 2026-08-06 — one wildcard serves all six hostnames. Stays open on internal mode, the no-API-provider handoff, the resume item, and migrating an already-serving estate without interrupting HTTPS. |
| 016 | [Vaultwarden identities and Rundeck bootstrap keyring](016-vaultwarden-identity-bootstrap/README.md) | Enrollment and cutover are done and the automation account drives every vault write. What remains is one decision: `users_collections` is empty, so the account reads the org as an Admin with `allowAdminAccessToAllCollectionItems` — org-scoped, not collection-scoped. |

## Recommended order

1. **Finish Uptime Kuma (404), then close 303.** It is the only baseline service that does
   not work, the fix path is known, and one monitoring provider being dead is why the
   MONITORS section of every status report says "unavailable".
2. **Make a deploy assert usability, not liveness.** Kuma passed four green runs while
   unstarted because "container healthy" was the whole test. Every app role wants one
   check that only an initialized application can pass.
3. **Decide 016's collection scoping.** Either grant per-collection and tighten, or amend
   the criterion to match the decision that has already been made in practice.
4. **011 before the next new guest allocation.** Six addresses are live under the flat
   model; fix the allocator before another deploy makes it harder to unwind.
5. **The browser legs.** Vaultwarden web-vault login and item CRUD, Authentik sign-in, and
   an app behind `forward_auth`. These are the last unobserved items on 400, 403, 405 and
   306, and they need a human at a browser.
6. **A media app role** — 504 wires the media stack but nothing deploys it. A `sonarr` role
   writing `media.<instance>` on deploy closes the loop.

### The config model, decided 2026-07-27 — implemented 2026-07-26

Three provenance classes, three homes:

| Class | Home | Why |
|---|---|---|
| `proxmox.yml`, `infrastructure.yml`, `apps/*.yml` | the **runner's `config/`**, reached from the UI both ways | must pre-date the vault; humans edit it; transport is Configure App / Get Config, not a repo |
| Bootstrap roots (Proxmox, Caddy DNS-01, Rundeck SSH, Vaultwarden admin + owner/automation unlock) | Rundeck **Key Storage** / Semaphore secret env | small fixed set that must open systems before Vaultwarden can serve application secrets; exact paths in 016 |
| `.generated/facts.yml` (~10 service endpoints) | **Vaultwarden**, file demoted to a secret-free cache | machine-written, never hand-edited, read by machines |

**Two bootstrap layers with resumable manual checkpoints.** `bootstrap-rundeck.sh` on a PVE
node runs as root, discovers what is discoverable (`pvesm`, `ip -o link`, hostname), prompts
for the six things it cannot know, issues its own Proxmox token via `pveum` rather than
asking for one, **writes the first class**, imports the jobs and stages Key Storage.
`Bootstrap Platform` in the UI then builds the lab. Authored config is never fused with a
secret again.

**No lab repo.** Carrying the authored shape in a private git repo cloned into `config/`
was reversed the day it was proposed: it bought only transport, and the Config job group
buys transport with parts that already exist. Durability is PBS plus
`config/.backups/<file>.<ts>`. The cost — history is point-in-time, not
per-commit-with-message — is accepted and recorded in 010.

**The runner is a managed guest.** `bootstrap-rundeck.sh` tags its own LXC `homelab-infra`,
writes `config/apps/rundeck.yml`, and records a `runner` registry key. Until it did,
`configure-pbs.yml` filtered the backup job on that tag and therefore excluded the single
host holding the platform's own configuration.

**Standing caveat.** The lab holds 57 hand-built LXCs and 4 VMs, **none tagged
`homelab-infra`**, so this repo ignores all of them by design — including the hand-built
Caddy that serves the same domain. That is correct per "manages what it creates", and it is
a decision to take deliberately rather than a surprise to hit mid-run.

## Retired trackers

`.claude/meta/` is the single backlog. Two earlier systems overlapped it:

- **`.claude/plans/`** — deleted 2026-07-25. Its six `design/` forms were all verified
  implemented in the tree by later tier work, and its two `concept/` notes were absorbed:
  the red-test-gate note's two `hosts:` defects by slice 102, its third by slice 502; the
  gate-wrapper note into `.claude/gate/README.md`.
- **`.claude/isotope-intake-backlog.md` + `.isotope/cultures/flux/`** — an abandoned
  migration into Isotope specimens. All eight flux specimens describe work that has since
  landed, and `.isotope/isotope.json` points at a checkout path that does not exist. Not
  yet removed — decide before it accrues more stale state.

## Cross-slice effects on record

Durable structural facts other slices depend on. Additions go here only when another slice
would be wrong without them.

**Execution and config**

- **`ansible/scripts/lab-run.sh` is the single job entry point.** Every Rundeck job step is
  `exec lab-run <playbook> [args]` with no path, venv or `cd` in it; paths come from
  `/etc/homelab-infra/lab-run.env`. Changing how jobs run is one edit, not nineteen.
- **The checkout refresh is a `git reset --hard`, armed only on a runner.** `LAB_REFRESH`
  defaults to 1 only when that env file exists, and refuses on a tree with uncommitted
  tracked changes. Both guards exist because the unconditional default destroyed
  uncommitted work in a development checkout.
- **Shared shell layer** under `ansible/scripts/`: `resolve-python.sh`, `config-doctor.sh`,
  `redact-config.sh`. `with-proxmox-env.sh` resolves its own Python — no job step puts the
  venv on `PATH`, so its hardcoded `python3` was the distro interpreter and all 15 Rundeck
  jobs failed identically at config parse.
- **`tasks/config/`** — `write-config-file.yml` (the one path any playbook writes into
  `config/`: backup, write, diff, prune to 20) and `run-doctor.yml`.
- **Secrets may come from the environment** (CONTRACT.md §5): `PROXMOX_API_TOKEN`,
  `PROXMOX_API_TOKEN_ID`, `PROXMOX_API_USER`, `VAULTWARDEN_ADMIN_TOKEN`, environment
  winning over file. The recommended `config/proxmox.yml` omits `api_token_secret`.
- **`rd` is not required.** The Rundeck REST API accepts the same job YAML the CLI sends.
  The git SCM plugin is retired: jobs are imported one-way from the repo, and a job edited
  in the UI is overwritten by the next reimport.
- **`artifacts/` and `ansible/.ansible/` are gitignored.**

**Registry and wiring**

- **Registry keys** (CONTRACT.md §3): `monitoring` (renamed from `uptime_kuma` by 303),
  `metrics`, `backups`, `runner` (written by `bootstrap-rundeck.sh`, not a playbook),
  `media` (instance-keyed, not role-keyed, because a lab runs several Sonarrs).
- **Fact-writing lives in the app playbooks.** Each baseline app records its own registry
  key in Play 3 before wiring, so a standalone deploy registers identically to a bootstrap
  run. `bootstrap.yml` writes no facts on their behalf.
- **`tasks/wiring/authentik.yml` dispatches on `wiring_identity_mode`** — catalog
  (Application tile only), oidc (OAuth2 provider, client creds returned), forward_auth
  (proxy provider); unwire removes whichever shape exists.
- **`tasks/app-wiring/` and `vars/media-wiring.yml`** — cloning the media playbook for
  another stack means changing which task files it loops over. Three optional `app:` keys
  (`media_kind`, `host`, `api_key`) enrol an app in media wiring, so a lab can wire apps it
  did not deploy.
- **`app.service_name`** on native app-defaults — 501 stops a native app by its unit name,
  which is not always the app name (PBS runs `proxmox-backup-proxy`).
- **`notifications` gained optional `user`/`password`/`token`**; consumers fall back to
  anonymous POST when no token is recorded, so `git pull` does not break an existing lab.
- **`prometheus-node-exporter` is in `tasks/guest-bootstrap.yml`** — every guest is
  scrapeable, including the observability host, which is why that role ships no
  node-exporter container (the package already holds `0.0.0.0:9100`).

**Vaultwarden writes**

- **`tasks/bitwarden/upsert-item.yml` compares before writing** and takes an opt-in
  `vault_item_merge` for roles that write the same item twice in a run with different
  field subsets. Without the merge the earlier write deletes what the later one stores —
  which is unrecoverable for Uptime Kuma's API key, since only a human can mint it.

**Both UIs** ship one job per app with `instance=<app>` baked in — no survey to fill for a
deploy. `Remove App`, `Restart App`, `Tail App Log` and `Rollback Container` keep their
parameters. Rundeck: 22 jobs across Bootstrap / Apps / Config / Maintenance.
