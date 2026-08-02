# Meta Index

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier. Slice template and workflow: [README.md](README.md).

## Status vocabulary

| Status | Means |
|---|---|
| `done` | Acceptance met. Nothing left. Do not reopen without a new slice. |
| `built` | Code written, both gates green, acceptance **not** yet observed on the live lab. |
| `open` | Not started, or started and abandoned mid-way. |

Gates (current as of 2026-08-01): `wsl bash -lc 'bash .claude/gate/lint.sh'` passes on the
`production` profile and now also runs `.claude/gate/jinja-parse.py`, which compiles every
Jinja expression in `ansible/`; `.claude/gate/test.sh` syntax-checks every playbook clean.
**Both gates are green** — slice 502 closed the last red one.

Counts: **15 done · 27 built · 4 open.**

**`Remove App` ran green-ish on 2026-08-02** — executions 13–21, API-triggered, against
the whole live baseline as step 0 of a deliberate teardown. Four of slice 501's five
acceptance items are met, including both the Docker and native LXC paths. The fifth is
disproved and re-opens **501**: removal is idempotent only while every platform provider
is still answering. `unwiring/caddy.yml` and `unwiring/authentik.yml` fail the playbook on
a connection error, so removing a proxy or an SSO provider strands every removal after it —
aborting exactly between unwiring and stopping the app, which is what the unwire-first
ordering exists to prevent. `unwiring/uptime-kuma.yml` is the only one of the four that
degrades correctly, and it is the pattern the other two need. Separately, **404** gains a
sharper fact: `GET /api/monitors` returns **200 `text/html`** (the SPA catch-all), so the
Kuma probe, delete and verify-assert all pass without a monitor ever existing.

**`Bootstrap Platform` ran green on 2026-08-01** — executions 7, 8 and 9, API-triggered, all
seven baseline services, converging to `changed=0` on the hosts that can reach it. That is
slice 500's acceptance event and 012's second criterion. It cost **twenty-five more
blockers** (16–40) on top of the fifteen below; full account in
[012's notes](012-runner-onboarding/notes.md). Live layout: Vaultwarden .10, Ntfy .11,
Caddy .12, Authentik on `sso-stack` .16, Uptime Kuma + Prometheus/Grafana on
`monitoring-stack` .14, PBS .15 (VM).

**The session's finding is that almost nothing here was idempotent.** Six of the
twenty-five (32, 36, 37, 38, 39, 40) are one defect in six costumes — an "is it already
there?" check that answered wrong. They were invisible to per-app testing because that only
ever runs a service once successfully; the chained bootstrap is the only thing that runs all
seven against a lab that already has them. Convergence is the property CLAUDE.md leans on
hardest ("re-running a deploy IS the update mechanism") and it was the least verified thing
in the repo.

**The gates do not measure whether the platform runs.** The first from-scratch runner
bootstrap (2026-08-01) hit **fifteen** blockers with both gates green throughout — eleven in
the seam between the repo and the machine that runs it (file modes, venv packages, shell
semantics, HTTP sessions, PVE version vocabulary, TLS trust, storage, node resolution,
inventory grouping) and four in shipped Ansible. `lint.sh` is ansible-lint and `test.sh` is
`--syntax-check`; none of the fifteen were syntax or style. `netaddr` is the sharpest case:
`.claude/gate/requirements-dev.txt` pinned it on 2026-07-06 *as the ipaddr filters' runtime
dependency*, so the gate venv had it and the runner venv never did. Full account in
[012's notes](012-runner-onboarding/notes.md). Worth adding: a smoke target that provisions
one throwaway guest, `shellcheck` over `rundeck/*.sh` + `ansible/scripts/*.sh`, and a
file-mode assertion.

**Three entries below are less true than recorded, and the 2026-08-01 click disproved them.**
**010** — the Config job group has never worked on a runner: `tasks/config/run-doctor.yml`
and `write-config-file.yml` were never in git, excluded by a bare `config/` in `.gitignore`,
so `Config Doctor` and `Configure App` fail on any machine that clones the repo. The
workstation was the only place they could have worked. **302** — the Authentik wiring is
verified for first-time creation only; the application lookup could not see what it had
created, so every app's *second* deploy failed at SSO wiring. **404** — its premise is false:
Uptime Kuma 2.5.0's entire HTTP surface is 16 routes, all GET. There is no REST write API in
any version, so monitor auto-registration cannot work as designed; socket.io is accepted as
later work.

**Two of the fifteen invalidate prior assumptions recorded here.** No `tag_*` inventory group
had ever existed (`keyed_groups: key: tags` matched nothing, since the plugin namespaces facts
as `proxmox_*`), so tag-based guest reuse — the basis of idempotency — never worked, and
`proxmox_clients` being absent meant `generate-ip.yml` never excluded an address already in
use. Separately, every app playbook wrote `.generated/facts.yml` two levels up into a path
nothing reads, so no service could record an endpoint another could load.

---

## Done (14)

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
| 013 | [Vaultwarden admin token self-capture](013-vaultwarden-token-capture/README.md) |
| 016 | [routing.access split](016-routing-access-split/README.md) |
| 100 | [unattended-upgrades dedupe](100-unattended-upgrades-dedupe/README.md) |
| 101 | [Stack key guard in template](101-stack-key-guard/README.md) |
| 102 | [Restart/tail assert ordering](102-restart-tail-assert-order/README.md) |
| 103 | [find-or-create-host docs](103-find-or-create-host-docs/README.md) |
| 200 | [write-generated-facts](200-write-generated-facts/README.md) |

## Built — awaiting live acceptance (28)

Every one of these is code-complete and gate-verified. **All but 504 clear on the same
event: a live bootstrap run against the lab** (slice 500's acceptance); 504 needs a
populated `config/` on the runner instead. Per-slice deviations and open questions live in
each slice's `notes.md` or in a "Built" section of its README.

| # | Slice | What live acceptance needs |
|---|---|---|
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | a second-domain deploy |
| 009 | [Identity-mode contract (routing.identity)](009-identity-modes/README.md) | one app deployed per mode |
| 010 | [Config provenance](010-config-provenance/README.md) | the bootstrap script run on a node. The Config job group (Configure App / Get Config / Config Doctor) is verified on the workstation; nothing the script does is |
| 012 | [Runner onboarding](012-runner-onboarding/README.md) | **first criterion observed 2026-08-01** — one command yields project + 19/19 jobs + Key Storage + config + a serving Vaultwarden. Remaining: the `Bootstrap Platform` click, Configure App / Get Config from the UI, and a job run naming its commit |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | Ntfy running (401) |
| 202 | [configure-pbs](202-configure-pbs/README.md) | PBS running (406) |
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | Caddy running (402) |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | an nginx lab — none exists; see below |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | Authentik running (403) |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | Kuma running (404) |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | OPNsense API creds |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | a Pihole — user runs OPNsense; low priority |
| 306 | [Reverse-proxy forward_auth](306-wiring-forward-auth/README.md) | Caddy path verified live 2026-07-25; browser sign-in leg + nginx path open — see below |
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | **deployed live and convergent 2026-08-01** — 1.37.1 serving on its own LXC, admin-token sink written in one pass (013), Caddy route present. Still needs the public-domain path and three `lab-*` maintenance commands exercised explicitly |
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
- **010/012's jobs have never been imported.** `bootstrap-rundeck.sh` was rewritten wholesale
  and none of it — `pveum` role creation, config authoring, project creation, Key Storage
  staging, job import — has run against a real node. Treat the first run as an experiment.

## Open (4)

011 was raised by the operator on 2026-07-26 reviewing the Rundeck runner handover; 014 was
split out of 010 on 2026-07-27; 015 was raised on 2026-08-01 during the first
from-scratch runner bootstrap. All are design defects in shipped code or gaps between the
documented model and the implemented one — none are new features.

| # | Slice | Why now |
|---|---|---|
| 011 | [IP allocation model](011-ip-allocation-model/README.md) | `generate-ip.yml` is a flat +1 walk with one global offset. The live lab addresses by function across three bands in a single /20; a flat allocator ignores that and erodes it on every deploy. |
| 014 | [Vaultwarden as the generated-secret store](014-vaultwarden-secret-store/README.md) | CLAUDE.md's secrets model is unimplemented — `community.general.bitwarden` exists only as `todo/` stubs. Every generated token lives solely in `facts.yml` on the runner, and PBS's is unrecoverable if lost. |
| 015 | [Wildcard DNS as the default path](015-wildcard-dns-default/README.md) | A lab on a stock ISP router has no DNS API. `none` already works as a silent no-op, but nothing documents it — README promises a DNS record per app, `config.example` presumes OPNsense, and one wildcard record would serve the whole lab. Separately, choosing a credentialed DNS provider at bootstrap authors a config that cannot work, because nothing collects its API key. |

**014 still gates the secret-durability claim** (010 and 012 landed 2026-07-26). Slice 013's
one-pass token capture is complete and was observed live on 2026-08-01.

## Recommended order

1. **011 before any provisioning job runs.** The moment Deploy Vaultwarden allocates an
   address, the flat model's output is on the wire and in the inventory. Cheaper to fix the
   allocator than to renumber guests.
2. **~~010 alongside it~~** and **~~012 with 010~~** — **both landed 2026-07-26**, as one
   change, for the reason recorded here: they shared `bootstrap-rundeck.sh`, both UI job
   sets and both READMEs, and landing them apart would have meant rewriting all four twice.
   They stayed two documents because their acceptance criteria do not interleave — 010's is
   "config exists, travels and validates", 012's is "one command and one click, and the
   runner runs current `master`".
3. **~~013 before the live bootstrap run.~~** **Landed and accepted 2026-08-01** — the
   generated admin token was written to the runner sink and bootstrap continued without a
   paste or re-run.
4. **~~Live bootstrap run.~~** **Completed 2026-08-01** — the full platform converged; the
   remaining per-slice exceptions are recorded above rather than inferred away wholesale.
5. **014 after the lab holds real secrets.** It is a durability change, not a correctness
   one, and its acceptance test (delete `facts.yml`, restore from the vault) is only
   meaningful once there is something in `facts.yml` worth losing.
6. **A media app role** — 504 wires the media stack but nothing deploys it. A `sonarr` role
   writing `media.<instance>` on deploy closes the loop; until then media apps join the
   wiring through the `app.media_kind` discovery path.

### The config model, decided 2026-07-27 — implemented 2026-07-26

Three provenance classes, three homes — 010 and 013 are implemented; 014 is the remaining
cutover needed to make the third row true at runtime:

| Class | Current home | Target after 014 |
|---|---|---|
| `proxmox.yml`, `infrastructure.yml`, `apps/*.yml` | the **runner's `config/`**, reached from the UI both ways | unchanged; these must pre-date the vault and remain human-editable |
| Proxmox token | Rundeck **Key Storage** / Semaphore env | unchanged; it bootstraps access to Proxmox |
| Vaultwarden admin token | runner `secrets.d/` / Semaphore env | remains external; Vaultwarden cannot store its own bootstrap credential |
| generated service credentials in `.generated/facts.yml` | the runner's gitignored `config/.generated/facts.yml` | **Vaultwarden** at job preflight; facts file becomes non-secret topology only |

**Two bootstrap layers, no manual seam.** `bootstrap-rundeck.sh` on a PVE node runs as root,
so it discovers what is discoverable (`pvesm`, `ip -o link`, hostname), prompts for the six
things it cannot know, issues its own Proxmox token via `pveum` rather than asking for one,
**writes the first class**, imports the jobs and stages Key Storage. `Bootstrap Platform` in
the UI then builds the lab. One command, one click. Authored config is never fused with a
secret again.

**No lab repo.** The morning's decision to carry the authored shape in a private git repo
cloned into `config/` was reversed the same day: it bought only transport, and the Config
job group buys transport with parts that already exist — `Configure App` writes an instance
file, `Get Config` returns the set, both with the diff in the job log. Durability is PBS plus
`config/.backups/<file>.<ts>`. The cost — history is point-in-time, not
per-commit-with-message — is accepted and recorded in 010.

**The runner becomes a managed guest.** `bootstrap-rundeck.sh` tags its own LXC
`homelab-infra`, writes `config/apps/rundeck.yml`, and records a `runner` registry key. Until
it did, `configure-pbs.yml:210-217` filtered the backup job on that tag and therefore excluded
the single host that holds the platform's own configuration — and `status.yml` could not see
the host it was running on.

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

From the 010 + 012 build (2026-07-26):

- **`ansible/scripts/lab-run.sh` is the single job entry point.** Every Rundeck job step is
  now `exec lab-run <playbook> [args]` with no path, venv or `cd` in it; paths come from
  `/etc/homelab-infra/lab-run.env`. Changing how jobs run is one edit, not nineteen.
- **The checkout refresh is a `git reset --hard` and is armed only on a runner.**
  `LAB_REFRESH` defaults to 1 only when that env file exists, and to 0 everywhere else; it
  also refuses on a tree with uncommitted tracked changes. Both guards exist because the
  unconditional default destroyed uncommitted work in the development checkout during this
  build — `lab-run.sh` ships in the repo, so it is present in every working tree, and
  `LAB_REPO` falls back to its own repo root.
- **New shared shell layer** under `ansible/scripts/`: `resolve-python.sh` (find a PyYAML
  interpreter — extracted from `with-proxmox-env.sh`), `config-doctor.sh` (validate `config/`
  against CONTRACT.md, all problems in one pass), `redact-config.sh` (print `config/` with
  secrets masked by structure walk).
- **New registry key `runner`** (CONTRACT.md §3) — written by `bootstrap-rundeck.sh`, not by
  any playbook, because the runner exists before any playbook can run. Descriptive only.
- **Secrets may now come from the environment** (CONTRACT.md §5): `PROXMOX_API_TOKEN`,
  `PROXMOX_API_TOKEN_ID`, `PROXMOX_API_USER`, `VAULTWARDEN_ADMIN_TOKEN`, read by
  `load-user-vars.yml` and `with-proxmox-env.sh`, environment winning over file. The
  recommended `config/proxmox.yml` now omits `api_token_secret` entirely.
- **New task directory `tasks/config/`** — `write-config-file.yml` (the one path any playbook
  writes into `config/`: backup to `.backups/<file>.<ts>`, write, diff, prune to 20) and
  `run-doctor.yml` (shared by `bootstrap.yml` Play 0 and the Config Doctor job).
- **New `Config` job group in both UIs** — Config Doctor, Configure App, Get Config, plus
  Reimport Jobs on Rundeck only. Rundeck job count 15 → 19; Semaphore templates 15 → 18.
- **`artifacts/` is gitignored** — Get Config writes unredacted restore points there.
- **The Rundeck git SCM plugin is retired**, not merely unused: job definitions are imported
  one-way from the repo, and a job edited in the UI is overwritten by the next reimport.

From the first live Rundeck run (2026-07-26):

- **`with-proxmox-env.sh` now resolves its own Python** (`059316a`). No job step in either
  UI puts the ansible venv on `PATH` — they called `"$VENV/ansible-playbook"` by absolute
  path — so the wrapper's hardcoded `python3` was the distro interpreter, which has no
  PyYAML. **All 15 Rundeck jobs failed identically** at config parse before Ansible was
  reached. Fixed in the one wrapper rather than in 15 job files; Semaphore's steps share the
  wrapper and inherit the fix. That episode is the direct argument for `lab-run.sh`.
- **`rd` is not required.** The Rundeck REST API accepts the same job YAML the CLI sends
  (`POST /api/47/project/<p>/jobs/import`, `Content-Type: application/yaml`). Nothing in
  the repo depends on the CLI being installed; the README's `rd` loop remains one valid path.
- **The runner is a documented host now, not a mystery.** LXC 13228 `pve-rundeck-4` on
  pve-host-3, project `homelab-infra`, checkout at `/var/lib/rundeck/homelab-infra` tracking
  `origin/master`, venv at `/opt/homelab-ansible`. Its Proxmox token was `root@pam!rundeck`
  (privsep off); slice 010 replaces that with `homelab-infra@pve` and a scoped role.
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
