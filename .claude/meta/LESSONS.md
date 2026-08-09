# Lessons and standing facts

Durable knowledge that outlived the slice that produced it. [INDEX.md](INDEX.md) is the
work queue and stays a table; this file is the prose. Per-session narrative belongs in a
slice's own `notes.md`, not here.

Add to this file only when another slice would be wrong without the fact.

## Standing lessons

**State survives boundaries the code assumes are fresh.** Four instances now:
`homelabinfra_config` carried across plays; a cloud template carried across runs; an
artifact deleted by one step that the next still expected; and a bind-mounted config file
whose inode the container kept after Ansible replaced it. Suspect it first on any "already
exists", "missing file", or "my change had no effect" symptom.

**Green is not working.** The gates are lint and syntax; they say nothing about whether the
platform runs. The first from-scratch runner bootstrap hit fifteen blockers with both gates
green, eleven of them in the seam between the repo and the machine that runs it. Uptime
Kuma is the same lesson one level up: the run was green, the app was not started. Worth
adding to the gates: a smoke target that provisions one throwaway guest, `shellcheck` over
`rundeck/*.sh` + `ansible/scripts/*.sh`, and a file-mode assertion.

**A deploy must assert usability, not liveness.** Uptime Kuma sat on its "choose a
database" setup screen from 2026-08-03 to 2026-08-08 — no admin user, no monitors, no
database — while four green bootstrap runs passed over it. Nothing in a deploy asserted
that an application was *usable*, only that its container was up, so an app waiting for a
human was indistinguishable from a working one. Worse, the role read Kuma's
`404 Cannot POST /setup` as "already initialised": **an error code interpreted as
success.** Every app role wants one check that only an initialized application can pass;
for Kuma that check is `GET /api/entry-page`. Treat any place a role reads an error status
as a success signal as the same bug.

**A wrong premise about an external API does not stay in one file.** "Uptime Kuma has a
REST API" was wrong, and it independently broke the wiring, the role's notification
channel, the Lab Status report and a deploy notification — four silent failures in four
files, each of which looked like working code in review, none of which any gate could see.
When a premise about a third-party surface turns out to be false, grep for every reader of
that surface before closing the slice. The cheap way to establish the truth is to read the
vendor's own source in the running container and then rehearse against a throwaway instance
of the same image; both together cost under an hour here and produced facts no amount of
reasoning would have.

**A guard can work while the path around it does not.** Break-glass recovery was
unreachable in vault mode twice — `config-doctor` and then `with-proxmox-env.sh` each
demanded a token that recovery exists precisely to do without. The gates cannot see this
class of defect; only injection testing finds it.

**Convergence was the least-verified property in the repo**, despite being the one the
day-2 model leans on hardest ("re-running a deploy IS the update mechanism"). It was
invisible to per-app testing, which only ever runs a service once successfully. Reaching
`changed=0` on 2026-08-08 cost eight defects, two of them real data risks: the vault upsert
deleted a field a later write in the same run restored, and Prometheus could not receive a
config change at all.

**Some acceptance criteria describe a design that has since been replaced**, so they can
never be ticked as literally written and the slice looks permanently open. 401, 402 and 404
all name `facts.yml` keys that slices 200 and 014 moved or deleted; 402 named HTTP-01 where
015 landed a DNS-01 wildcard. Where the shipped mechanism satisfies the *intent*, record it
met against the shipped shape and say so — do not leave it unticked.

## Where the platform stands

A second estate — separate from the hand-built lab — runs seven guests on pve-host-3, all
tagged `homelab-infra`, all built by this repo:

| | |
|---|---|
| **Converges** | `Bootstrap Platform` re-runs to `changed=0` on every host (execution 34, 2026-08-08). That was the last open item on 500 and it took eight defects to reach. |
| **Serves HTTPS** | One `*.wasitacatisaw.cc` Let's Encrypt certificate via Cloudflare DNS-01 covers all six estate hostnames; every one verifies. |
| **Keeps its secrets in the vault** | Vault mode, `facts.yml` secret-free, the automation account drives every write, and the fail-closed guarantee has been tested by injection. |
| **Backs itself up** | PBS holds 30 snapshots — five consecutive nights for each of six guests, unattended. |
| **Is monitored** | Prometheus scrapes all seven guests. Uptime Kuma is initialized, holds an API key, and its wiring registers monitors over socket.io — verified end to end against 2.5.0, though not yet on the lab's own instance. |

## The config model, decided 2026-07-27 — implemented 2026-07-26

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

## Architecture facts other slices depend on

Registry key *shapes* are not here — `ansible/vars/CONTRACT.md` §3 is the single
authoritative source for the `homelabinfra_infra` topology, and §6 for known conflicts and
their owning slices. What follows is the structural knowledge that lives nowhere else.

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

- **Uptime Kuma is driven over socket.io, never REST** — no version has a REST monitor API,
  and its catch-all route answers any unmatched path with 200 `text/html`, so a status-only
  check reports an API that is not there. `ansible/tasks/kuma/` holds the shared
  conversation (session, call, poll, drain); the credential is `monitoring.admin_password`,
  not the API key. Any new Kuma reader goes through those helpers.
- **`monitoring` was renamed from `uptime_kuma`** by 303. `media` is instance-keyed, not
  role-keyed, because a lab runs several Sonarrs. `runner` is written by
  `bootstrap-rundeck.sh`, not by a playbook.
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
  `vault_item_merge` for roles that write the same item twice in a run with different field
  subsets. Without the merge the earlier write deletes what the later one stores — which is
  unrecoverable for Uptime Kuma's API key, since only a human can mint it.

**Both UIs** ship one job per app with `instance=<app>` baked in — no survey to fill for a
deploy. `Remove App`, `Restart App`, `Tail App Log` and `Rollback Container` keep their
parameters. Rundeck: 22 jobs across Bootstrap / Apps / Config / Maintenance.

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
