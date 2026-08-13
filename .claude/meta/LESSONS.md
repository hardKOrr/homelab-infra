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

**A job that has never run is presumed broken.** Not a figure of speech — a measurement.
The four day-2 Rundeck jobs were executed for the first time on 2026-08-12 and **three of
the four failed on their first line of real work**: two had no connection user and could
not reach a single guest, one used `${option.x}` inside a bash script step and died at
`bad substitution`. All three had been gate-green for weeks. The same day, a fourth job's
health probe was found to have never once executed, taking a fatal safety gate with it.
Sampling three from four is not bad luck; it is what shipping-without-executing looks like.
When a slice says "built" and no execution is on record, the honest status is "unverified".

**An assertion gated on an optional value is not an assertion.** `rollback-container.yml`
probed the app port only `when: _rb_port | length > 0`, and `_rb_port` came from the
instance config file — where ports are never declared, because they live in
`vars/app-defaults/`. So the probe, and W4's fatal gate behind it, silently skipped on every
app for the whole of their existence. Two rules follow: resolve such a value from every
source that can supply it, and when it still cannot be resolved, SAY SO in the log instead
of skipping quietly. A skipped check and a passed check look identical in a green run.

**One namespace, two meanings, is a defect waiting for its second user.**
`homelab-infra/media/<instance>` is where every media app stores its secret, and
`load-user-vars.yml` merges the whole vault contract into `homelabinfra_infra`, which is
also the media wiring registry. Every app was both a credential holder and a wirable app, so
the overload was invisible — until Jellyfin, which has a password but is not an indexer, a
download client or an *arr. It broke every `Wire Media Stack` run from the morning it
shipped. Neither file was wrong alone. Before adding a key to a shared namespace, ask what
reads the whole namespace.

**Green is not working.** The gates are lint and syntax; they say nothing about whether the
platform runs. The first from-scratch runner bootstrap hit fifteen blockers with both gates
green, eleven of them in the seam between the repo and the machine that runs it. Uptime
Kuma is the same lesson one level up: the run was green, the app was not started. Worth
adding to the gates: a smoke target that provisions one throwaway guest, `shellcheck` over
`rundeck/*.sh` + `ansible/scripts/*.sh`, and a file-mode assertion.

**The runner can be disarmed by the day-2 automation this repo installs.** On 2026-08-12
unattended-upgrades reinstalled `openjdk-21-jre-headless` at 06:24 under a Rundeck JVM that
had been running nine days. The JVM could no longer `fork`, so **every job on the platform
failed before reaching Ansible**, with an opaque error naming a "spawn helper", no mention
of Java versions, and nothing wrong in the repo:

```
IOFailure: Cannot run program "/bin/sh": Failed to exec spawn helper
```

`systemctl restart rundeckd` fixes it in about a minute. Two general points: a job failure
whose text names no playbook and no task is a **runner** fault, so check the runner before
reading a single line of Ansible; and the runner is the one host where an in-place library
upgrade needs a service restart to take effect, which unattended-upgrades will not do for a
JVM holding an open jar.

**One git checkout serves every job, and `LAB_REFRESH=1` pulls at job start.** Two jobs
launched at the same moment collide:

```
Unable to create '/var/lib/rundeck/homelab-infra/.git/index.lock': File exists
```

Rundeck's per-job execution limit prevents the *same* job overlapping itself and nothing
else — nine different Deploy jobs fired together are nine racing `git pull`s. Deploy jobs
are meant to be run one at a time; anything that launches them in a batch has to wait for
each to finish. Eight of nine survived the race on 2026-08-12, which makes this a defect
that will usually hide.

**A deploy must assert usability, not liveness.** Uptime Kuma sat on its "choose a
database" setup screen from 2026-08-03 to 2026-08-08 — no admin user, no monitors, no
database — while four green bootstrap runs passed over it. Nothing in a deploy asserted
that an application was *usable*, only that its container was up, so an app waiting for a
human was indistinguishable from a working one. Worse, the role read Kuma's
`404 Cannot POST /setup` as "already initialised": **an error code interpreted as
success.** Every app role wants one check that only an initialized application can pass;
for Kuma that check is `GET /api/entry-page`. Treat any place a role reads an error status
as a success signal as the same bug.

As of 2026-08-09 all seven app roles carry one, and both role templates fail closed until
the author replaces the placeholder. The probes that work fall into four shapes, and a new
role should copy the closest one rather than invent a fifth:

| Shape | Where | Probe |
|---|---|---|
| Authenticate and be named back | authentik, pbs | `/api/v3/core/users/me/` asserted against the expected username; the PBS token asserted against `/api2/json/access/permissions` |
| Perform the real operation, and prove the anonymous one is refused | ntfy | authenticated publish 200, anonymous publish 401/403 |
| Compare what the RUNNING process loaded against what this run wrote | vaultwarden, observability | `/api/config`'s `environment.vault` vs the templated `DOMAIN`; Prometheus's active target set vs the rendered target list |
| Ask the app what stage it is at | uptime-kuma | `/api/entry-page` reports `setup-database` or `entryPage` |

Two facts cost a live probe each and are worth not rediscovering. Vaultwarden's
`/api/config` `version` is the bundled **web-vault** version (`2026.6.0` while the server
binary reports `1.37.1`) — comparing it against the installed release fails every run. And
Grafana's `/api/datasources/uid/<uid>` proves a datasource *exists*; only
`/api/datasources/uid/<uid>/health` proves Grafana can *query* it, which is the difference
between provisioning that landed and dashboards that render blank. Prometheus target
*health* is deliberately not asserted — a guest that happens to be down is not a reason to
fail a deploy — only that the scraped set matches the rendered one and something scrapes.

**A wrong premise about an external API does not stay in one file.** "Uptime Kuma has a
REST API" was wrong, and it independently broke the wiring, the role's notification
channel, the Lab Status report and a deploy notification — four silent failures in four
files, each of which looked like working code in review, none of which any gate could see.
When a premise about a third-party surface turns out to be false, grep for every reader of
that surface before closing the slice. The cheap way to establish the truth is to read the
vendor's own source in the running container and then rehearse against a throwaway instance
of the same image; both together cost under an hour here and produced facts no amount of
reasoning would have.

**Some properties are untestable until the estate is large enough to test them.** Proving
that a monitor's DOWN alert reaches Ntfy needs three services, not two: with only Kuma and
Ntfy registered, stopping Ntfy kills the notifier and stopping Kuma kills the detector, and
either experiment produces a plausible failure that proves nothing. A third, expendable
target had to be deployed before the alert path could be exercised at all (2026-08-09,
slice 303). This is a property of the lab, not of the code — when acceptance stays open for
a long time, check whether the estate can express the test before assuming the code is at
fault. The same shape applies to 301/305, which have no live target of any kind.

**A container boundary is an identity boundary, and ownership does not survive it.** An
unprivileged LXC maps its whole id range into the node's `100000+` block, so a host uid
below that is not representable inside the guest at all: the directory is present, mounted,
correct — and reads as `nobody:nogroup`, refusing every write. Radarr is the app that says
so out loud (`Folder '...' is not writable by user 'abc'`); the same wall is waiting for
anything that writes to host storage from an unprivileged guest. The fix is one id that
means the same thing on both sides — an `lxc.idmap` passthrough plus a matching
`/etc/subuid` and `/etc/subgid` grant — never a `chown -R` across the lab's library.

Two corollaries worth keeping. **A generic default uid will collide with a real account**:
`puid: 1000` on a Debian node is whoever was created there first, which here silently made
`civicfs` — the file server of a domain being decommissioned — the owner of the media
library. The platform now creates and owns `homelab-infra` at 1313 for exactly this reason.
And **`pct set -mpN` hotplugs into a running container**, which has to re-apply an apparmor
profile and fails outright; a stopped container takes the same command unconditionally, and
the guest had to restart to see the mount anyway.

**A `no_log` task cannot report itself.** The credential is in the request, so the task is
rightly silenced — but that silences the *server's answer* too, and a deploy then fails with
nothing but `censored`. Reading why Radarr refused a root folder cost a hand-run curl against
the container, and the response body held no secret at any point. Any `no_log` task that can
fail on what the server said needs `failed_when: false` plus a following task that re-raises
from the response alone. This is the same defect shape as 016's silenced collection grant,
which cost three passes across two sessions.

**Green is not even templated.** Both gates passed over an expression that could not render:
a `vars:` entry is a templated STRING even when its expression ends in `| int`, so range
arithmetic on it died with "can only concatenate str (not int) to str" — at run time, on the
live lab, after the run had already changed the node. Syntax check and lint see a perfect
playbook. Verify an unfamiliar Jinja construction by *running* it against a throwaway
playbook before it reaches a job; that cost two minutes here and one failed execution.

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
| **Is monitored, and says so** | Prometheus scrapes all seven guests. Uptime Kuma holds three monitors registered by deploys over socket.io, and on 2026-08-09 a real Grafana outage produced a DOWN and a recovery message in Ntfy's own database — the first time the platform has been observed telling anyone that something broke. |
| **Checks that it is usable, not merely up** | Every app role ends on a probe of the application layer. All seven ran green live on 2026-08-09 (executions 42/43/44 for Vaultwarden, Observability and PBS; the other four in earlier bootstraps). |

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
- **Addressing is static, and a VLAN is a network.** Every guest gets an address the
  platform chose; DHCP happens only where a network's `cidr` says the literal `dhcp`, never
  as a fallback when allocation is hard. `networks.<name>` carries its own tag, subnet and
  gateway, so an app changes VLAN by changing one name in its instance file, and pools
  (`networks.<name>.pools`) express function bands inside a network while a lab is still
  flat. `scripts/allocate-ip.py` decides and explains every refusal; an exhausted pool
  fails rather than spilling into the wider subnet. Slice 011.
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
