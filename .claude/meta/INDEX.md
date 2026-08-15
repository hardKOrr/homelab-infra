# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**9 live · 37 archived in [done/](done/) · 3 unreachable in [no-target/](no-target/).**
304 and 502 closed 2026-08-12, both by running things. 306 and 601 closed 2026-08-13/14,
same way. **015 closed 2026-08-15 by operator decision** — see "The second estate" below.

## Start here

**Pick the top unchecked row of [the work queue](#the-work-queue) and implement it.**
Every row is code work in this repo, needs no lab access, no browser and no hardware, and
is finished when both gates pass. Do not go looking for something else to do first, and do
not reorganize this file instead of working it.

Two things that are *not* work, and must never be presented as the next thing to do:

- **[Observe if it happens](#observe-if-it-happens)** — checks that need the lab. Nothing
  depends on them. They are not blocking, and reading them is not progress.
- **Slice `Remaining` boxes.** Many describe third-party behaviour rather than this repo's
  code. The work queue below is the authority on what is left to build.

Gates (both green, both under WSL):
`wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
and the same for `test.sh`.

## The work queue

The theme: **a deploy that could not do what it was asked must not exit 0.** An audit on
2026-08-10 found 22 places that catch a failure and print it as a `debug` message. Eight are
correct, three already rolled up into a fatal, and **eleven let a deploy report success
while the thing it was deploying was not actually wired** — the same class as execution 67,
which wired nothing and exited 0.

### What the 131 `failed_when: false` uses actually are

Classified 2026-08-10, because "mostly fine" was asserted once without evidence and is not
good enough. `failed_when: false` is only defensible when something downstream reads the
result; the question is how many do.

| Class | Count | Verdict |
|---|---|---|
| `include_vars` on an optional file — absent file means use defaults | 78 | not a failure; an optional input |
| Registered, and a `fail`/`assert` reads it | 16 | the "suppress, then fail readably" idiom |
| Registered, read only by a `when:` branch — probe-to-decide (`Check whether this guest runs Docker`, `Check installed vaultwarden version`, `Read the existing compose env` for secret continuity) | ~25 | correct: absent means take the other branch |
| Registered, read by a `fail` that keys off a loop item rather than the register name | ~3 | correct — `roles/servarr:436` re-raises via `Report a refused root folder`; a name-matching audit misses these, so do not trust one |
| **Unregistered and not `include_vars`** | **9** | listed below — the only ones where the result is genuinely never inspected |

The nine: `notify.yml:67` (**was the real one — fixed, W6.5**), `migrate-servarr.yml:155,271`,
`roles/vaultwarden:149` (mkdir), `bitwarden/cleanup.yml:9` and both Pihole `Close API
session` tasks and `kuma/poll-once.yml:47` (session teardown and a protocol ping — nothing
downstream can act on them), plus one comment-line false match in `load-user-vars.yml`.

So: one genuine shrug in 131, and it was the one that mattered most, since every
notification in the repo went through it. This was a bounded defect, not a culture — but
that conclusion is now measured rather than assumed.

**The standing rule, set 2026-08-10:** if something did not work, the run fails. "The real
work succeeded, only the reporting failed" is not an exemption — it is the exact shape of a
problem discovered months later. Non-fatal (`degradation_fatal: false`) is reserved for
genuine partial successes where a named, working fallback is in place, and every use needs
a comment saying what the fallback is. There are currently none.

**W1–W5 are done, 2026-08-10.** Both gates green, and the ledger's behaviour is proven by
a scratch play: an empty ledger passes, a non-fatal-only ledger passes, and two fatal
entries fail naming both.

**The queue is empty as of 2026-08-11** — W6 and W7 closed the last two rows. There is no
top unchecked row to pick up; the next session needs work put here first, and the two
sections below ("Observe if it happens", slice `Remaining` boxes) are still not it.

### Why nothing closed while a lot got built, 2026-08-12

A session asked the fair question: eight commits of real work and not one slice moved to
[done/](done/). The answer is that the work was **queue rows** (W1–W7, then Jellyfin), and
queue rows are not slices. Every live slice is code-complete; what each waits on is a live
observation. So building more never closes one.

**The acceptance was also behind the lab.** Executions 55–102 had already satisfied criteria
nobody went back to tick: 504 lost three boxes and 505 lost five on inspection alone, no code
touched. Tick the box in the same session as the run, or a later session re-runs the job
(which is exactly what the access-group remediation cost, twice).

### That list was worked the same day — executions 103–121

Every row above the browser line was run. **Six defects, five of them in code that both
gates had passed and that had never been executed once.**

| Job | Defect | Where |
|---|---|---|
| Tail App Log, Restart App | No connection user — the Proxmox inventory sets none, so both connected as `rundeck@` and every guest was UNREACHABLE | `2694ce7` |
| Rollback Container | Health probe gated on `_rb_instance_config.app.port`, but ports live in `vars/app-defaults/` — so the probe, and **W4's fatal gate behind it**, had never once fired | `2694ce7` |
| Migrate Servarr | `${option.x}` inside a bash script step — died on line 4 | `2694ce7` |
| Deploy `<arr>` | **A new API key on every deploy.** Continuity read `config.xml`, where a platform-supplied key never appears; three consecutive Prowlarr deploys, three different keys. Every Prowlarr Application and registry entry holding the old one goes stale | `726f947` |
| Wire Media Stack | **Jellyfin broke it permanently the morning it shipped** — its vault credential item lands in the media registry via `load-user-vars.yml:56` and W3 makes an unplaceable entry fatal | `c3a1cca` |
| Wire Media Stack | The fix's first version referenced a fact from inside the same `set_fact`; gates green, execution 121 red | `c3a1cca` |

**The gates cannot see any of this.** Lint and syntax-check passed every one of these files
on every commit. The only instrument that found them was execution. Treat "never run" as
"presumed broken", not as "built".

### What is left, ranked by cost

| Click | Closes | Cost |
|---|---|---|
| A migration that completes | **504's adoption PUT** — the last box on it | blocked, see below |

**400 closed, 2026-08-14.** Rundeck executions 138–140 ran Vaultwarden's update check,
restart and log tail. The update command reported installed/latest `1.37.1`, the restart
produced a clean stop/start and the log tail showed successful requests from the new
process. Combined with the browser CRUD check, that closes every acceptance item.

**403 closed, 2026-08-14.** Authentik execution 144 applied the one-time PostgreSQL mode
transition from the old declaration to runtime-stable `0700`. Immediate execution 145 used
pushed revision `8138199`; database and Redis directory tasks were both unchanged and the
full recap was `changed=0`, `unreachable=0`, `failed=0` on both hosts.

**The browser session is done, 2026-08-14.** Vaultwarden login and CRUD succeeded at the
public hostname, completing the browser leg before executions 138–140 closed 400. Grafana
login succeeded with the generated credentials retrieved from Vaultwarden, closing 405.
The datasource, dashboard data, scrape targets and idempotent re-run were already proven.

**601 closed and `Deploy SABnzbd`'s api_key proof is done, 2026-08-13/14** — both run
entirely over the Rundeck REST API via SSH, no browser needed (see the
`live-lab-access` memory). The Config group's last untested job, `Configure App`,
correctly refused a no-op write on `sabnzbd` (execution 133 — its own guard, not a
defect), then succeeded once given a real, operationally-inert override (execution
134). `Deploy SABnzbd` ran twice more (128, 129, after one transient first-boot
timeout at 126); both show `changed: false` on the API-key tasks, so the key held.
Full account in [601/notes.md](done/601-rundeck-jobs/notes.md).

**The migration is deliberately not wanted yet** (operator, 2026-08-12). The platform's media
stack mounts `/friends-pool/homelab-media` — empty, 114 G — while the old *arrs use
`/mnt/media`, 40 T. That is intentional: the platform's library is separate, one open slice
is a full teardown and rebuild, and the old apps exist as migration *test material*, not as a
cutover target. Execution 120 stopped exactly there, which is the path guard working. Do not
"fix" the storage mismatch.

010/012/013/014 need a bare-metal bootstrap. Those are
the only genuinely blocked ones now — **304 was never blocked at all**: its "missing"
OPNsense credentials had been in the repo's gitignored `.env` for days.

### The second estate is declared live — 2026-08-15

`config/infrastructure.yml` on the runner now carries a `domains:` map:
`personal` = wasitacatisaw.cc (`default: true`), `foxglove` = foxglove-collective.com.
That closed three of 008's five boxes on the day it landed:

| Proof | Evidence |
|---|---|
| Declaring `domains:` changes nothing for apps without `routing.estate` | Execution 146, `Deploy Ntfy`: `changed=0` on both hosts, `facts.yml` byte-identical, no `estates` key written |
| An undeclared estate fails fast with the named assert | Scratch play on the runner — `routing.estate: nosuchestate` hit `Assert a named estate is declared` |
| The estate overlay does not leak the default estate's identity | The same play: `foxglove` resolved to `domain=foxglove-collective.com` with `sso` replaced **whole** by `{provider: none}` |

**015 closed here too, by operator decision.** Its last box was a live per-host →
wildcard migration. The lab is already converged — Caddy 168000010 holds
`wildcard_.wasitacatisaw.cc.crt` with `automate: ["*.wasitacatisaw.cc"]` — so
`_caddy_pending_wildcards` is empty and the transition code **cannot fire on a re-run**.
Proving it would mean deleting a working certificate to re-enter the migration; the
operator declined. So the outage-free transition built in `71118af` is reviewed and
**never executed** — presumed broken until a lab actually migrates.

**DNS scope for the estate is decided: foxglove only** (operator, 2026-08-15).
`infrastructure.dns.provider` is global, and the lab's existing Unbound overrides point at
the **hand-built** Caddy at 192.168.7.20, not the platform one at 192.168.0.10 — so turning
it on globally would move each wasitacatisaw.cc hostname to the platform edge as it is next
deployed. The estate gets its own `estates.foxglove.dns` entry instead, and the default
estate's records are not touched.

### What the second estate WAS blocked on — a secrets-delivery gap, closed 2026-08-15

**Read this section as history.** The gap it describes is real and its measurement stands;
what changed is that the platform now has a post-cutover route — the **Store Secret** job,
see "DNS for the estate" below. Two claims here are also now wrong and are left in place
because the reasoning that produced them is the lesson: `load-user-vars.yml:145` no longer
maps `estates/<n>/dns` to `dns_challenge` (that is `estates/<n>/reverse_proxy` now), and
"the cutover job is the estate import path" was Likely, then measured false at execution
147. A `Likely` reasoned from source is worth exactly one run.



Both remaining lab writes carry a credential, and **neither has a delivery path that does
not put a secret on the runner's disk first**:

- `domains.foxglove.dns_challenge.api_token` — the Cloudflare token for
  foxglove-collective.com (verified against the zone API, 2026-08-15, from the repo's
  gitignored `.env`)
- `estates.foxglove.dns.api_key` / `.api_secret` — the OPNsense pair (also verified live;
  the API answers at 192.168.13.1)

`load-user-vars.yml:145` reads these from Vaultwarden — item
`homelab-infra/estates/<name>/dns` becomes `domains.<name>.dns_challenge` — and
`lab-run.sh:280` materializes every item through `bw list items`, so **an item created by
any means is picked up with no code change.** The only importer in the repo is
`vaultwarden-cutover.yml:146`, which loops `infrastructure.domains` and upserts each
estate's block. Its three preconditions (`proxmox.api_token_secret`,
`vaultwarden.admin_token`, `ANSIBLE_PRIVATE_KEY_FILE`) are all satisfied *from the vault*
post-cutover by `lab-run.sh`, so **the cutover job is re-runnable and is the estate import
path** — Likely, reasoned from the source, not yet run.

**The cutover job is NOT that path — measured, execution 147.** `rundeck/jobs/
vaultwarden-cutover.yaml` exports `LAB_SEED_MODE=1`, and `lab-run.sh` refuses it outright
once the vault-mode marker exists: `runner is already in Vault mode; LAB_SEED_MODE cannot
bypass Vaultwarden`. It never reaches the play, so the play's own preconditions — which
*are* satisfiable from the vault post-cutover — are irrelevant. **A secret authored after
the one-time cutover has no supported route into Vaultwarden.** Anything needing one today
must be reasoned about with that in mind; the estate's Cloudflare token consequently lives
in the runner's `config/infrastructure.yml` (0640 `rundeck:rundeck`), which is the same
guest-held-credential exception as `/etc/caddy/caddy.json`, not a fix.

The operator authored the token by hand on 2026-08-15, after three attempts to push the
seeded file were **refused by the permission classifier** (`pct push` and `scp` alike). The
estate map itself, carrying no secret, pushed fine.

**407 closed on that token — executions 148 and 149.** The foxglove wildcard issued via
DNS-01 in about 40 s, each estate policy carries its own distinct token ahead of the
catch-all (compared by hash, never printed), the catch-all carries none, and the re-run was
`changed=0` on the Caddy host.

### 008 and 009 closed too — and cost three defects on the way, executions 150–161

The estate got its own Authentik (`authentik-foxglove`, LXC 168000200 on its own
`sso_stack_foxglove` host) and an app deployed into it, exercised through every identity
mode and then removed. Full account in [008/notes.md](done/008-estate-contract/notes.md).

**First, the platform could not click a second instance at all.** Every per-app Deploy job
hard-coded `instance=<app>`, while 008's contract says an estate's SSO is an ordinary app
deploy. `a00430d` makes `instance` a required option **prefilled with the app's own name**,
so one-click stays one click and a second instance is a different value in one field.

| Defect | What actually happened | Fix |
|---|---|---|
| `roles/authentik` wrote every instance to the fixed vault item `homelab-infra/sso`, and read token continuity from the unscoped fact | Execution 151 exited 0 having overwritten the **platform's** vault item and handed the new instance the platform Authentik's **own API token** — same SHA-256 in both `.env` files. Two identity providers, one credential | `b68a117` — item and continuity are `homelab-infra/estates/<name>/sso` for an estate instance |
| The estate therefore had no token under `estates.<name>.sso` | Execution 152: the first app into the estate failed the wiring contract assert. The assert worked; the naming under it did not | same |
| `Remove App` with `delete_data: true` deleted the Compose project and named volumes, but not the **bind-mounted** data path | Execution 153 reported success with the whole Postgres cluster still on disk. Execution 155 then minted a fresh password the surviving database refused — 60 readiness retries, cause visible only in `docker logs` | `9c4393e` — deletes bind-mounted data/config, and keys every app default by `{{ instance }}` |

**All three were gate-green, and two of the three exited 0 while doing damage.** The
useful cheap habit from this round: the fix's own expressions were proven in a scratch play
against four fact shapes — including "named estate with nothing recorded yet, must NOT
inherit the default estate's token" — before anything was pushed.

**One live fact the next session needs.** The estate's Authentik is still running and
empty. The LAN-DNS half is dealt with below.

### DNS for the estate — and DNS wiring had never been reachable at all, 2026-08-15

The operator asked for the OPNsense credential to be *copied* from the default estate to
`foxglove`, since one firewall serves both. **There was nothing to copy, and reading for
the copy found three defects, all by inspection, none of which any gate can see.**

| Defect | Why it was invisible | Fix |
|---|---|---|
| **DNS wiring could not fire in any lab, ever.** Wiring reads `homelabinfra_infra.dns` — the registry. Nothing writes a `dns` registry entry: bootstrap writes one key per service **it deploys**, and an external firewall is not one. The cutover imports `api_key`/`api_secret`/`token` into `homelab-infra/dns` and no `provider`. So `dns.provider` was always absent and every app playbook's `dns.provider \| default('none') != 'none'` was always false, whatever `infrastructure.yml` declared | The lab has run with `dns.provider: none` throughout, so nothing ever contradicted it. 304 closed on credential verification, not on a record being written | `load-user-vars.yml` — authored `infrastructure.dns` is the BASE of the registry entry, vault/facts overlaid on top, so a pre-cutover file credential still loses to the vault |
| **The estate's two DNS credentials shared one vault item.** `estates/<n>/dns` mapped to `domains.<n>.dns_challenge`, so an OPNsense key stored for record wiring would arrive in the ACME block and the caddy role would issue that estate's certificates against `provider: opnsense` | Only one estate credential had ever existed (407's Cloudflare token), and it was the challenge one | Split, mirroring the global pair: `estates/<n>/reverse_proxy.dns_api_token` = ACME DNS-01, `estates/<n>/dns` = record wiring. Cutover writes both |
| **A secret authored after cutover had no route into the vault** (measured at execution 147) — so per-estate DNS would have meant a second credential hand-written onto the runner's disk, the thing cutover exists to end | The gap was named in this file and left as an operator chore | New **Store Secret** job (`playbooks/maintenance/store-secret.yml`): one field of one canonical item, value as a Rundeck secure option, through the environment not argv, `no_log` throughout |

**The result is that no credential is duplicated.** `domains.<name>.dns` carries the
non-secret half only — provider, host, validate_certs — and `resolve-estate.yml` overlays
it on the credential the estate already inherits from `homelab-infra/dns`. One firewall,
two estates, one stored key. It applies to the default estate too, so
`domains.<default>.dns: {provider: none}` is what holds wasitacatisaw.cc's hand-built
records out while foxglove's zone is managed — which is the operator's 2026-08-15 scope
decision expressed in config rather than in a global switch nobody could aim.

Proven in a scratch play against three fact shapes before anything was pushed: foxglove
resolves to `opnsense` + the inherited key + its own host; both default-estate paths stay
`provider: none`; and an estate holding its own credential keeps it with no default-estate
leak.

**Then run, executions 162–166, and every part of it worked on the first live click.**

| Execution | What it proved |
|---|---|
| 162 | `Reimport Jobs` — 31 imported, `store-secret.yaml` among them |
| 163, 164 | **Store Secret's first live runs.** `homelab-infra/dns` gained `api_key`, then `api_secret` with `merge: true` keeping the first. Both readbacks verified by upsert-item's own exact-field assert; `failed=0`, and no file was written anywhere |
| 165 | `Deploy Authentik` for `authentik-foxglove`. **`Wire OPNsense \| Add host override` fired — the first time DNS wiring has executed in this lab at all** — then applied the Unbound configuration. `auth.foxglove-collective.com` now resolves on the LAN to 192.168.0.10, the platform Caddy |
| 166 | The same deploy again: `changed=0` on **both** hosts, the override neither added nor updated. Idempotent |

The credential reached the vault through the new job with no secret at rest on the runner
and none in any command line — the payload went to the Rundeck API over stdin, and the job
hands it to Ansible through the environment.

**Watch for a stale assumption underneath this.** The scope decision was reasoned partly
from "the lab's existing Unbound overrides point at the hand-built Caddy at 192.168.7.20".
Measured 2026-08-15: `vaultwarden.wasitacatisaw.cc` and `sonarr.wasitacatisaw.cc` both
resolve to **192.168.0.10**, the platform edge, already. The estate-scoped decision still
stands and nothing about it was changed — but if it is ever revisited, re-measure rather
than re-reading that sentence.

### 306 closed, 302 and 403 advanced — 2026-08-13, forward_auth sign-in confirmed live

Sonarr denied akadmin (superuser, but not a member of `homelab-users` — application-access
group bindings are not superuser-bypassed by design, see 403's notes.md). Operator added
akadmin to `homelab-users` in the Authentik UI and retried: Sonarr redirected to Authentik's
login flow, akadmin signed in, and the browser returned to Sonarr authenticated.

That is the full `forward_auth` browser leg 306, 302 and 403 were all waiting on.
**306 is done** — its only remaining item was the Nginx path, unreachable in this lab for
the same reason 301 is parked in `no-target/`, so it does not block closing. **302** ticks
its forward_auth redirect item; `oidc`'s sign-in leg and the unwire-then-denied check are
still open. **403** ticks three of five acceptance items; the registry-token check and an
idempotent re-run remain.

### W1–W7 ran live, 2026-08-11 — executions 75–78

Recorded here because "done" and "never run live" were both true of this table at once,
and nothing on the page dated either. **A claim of live proof is only about the commit it
was measured at.** Every row above was gate-green and unexecuted until execution 78.

Four of the five W commits were also **unpushed**, and the runner tracks `origin/master`,
so the lab could not have run them. It was still on `39ef809` (2026-08-09). A future
session's first question is `git log origin/master..master`, not the gates.

| Execution | Result | What it proved |
|---|---|---|
| 75 | failed | qBittorrent's own storage assert. The shipped `download_path` was `/mnt/downloads`, the lab mounts `/mnt/data/media`, so a literal default could never satisfy the role's rule — the app was undeployable on the first click. Fixed by deriving it from the declared mount. |
| 76 | failed | The app deployed, then the WebUI login assert failed on a login that had **succeeded**. qBittorrent 5.2.3 answers an accepted login with 204 and no body; `login.yml` compared the body with the 4.x `"Ok."`. Fixed. |
| 77 | failed | **The degradation ledger fired for the first time, live, and caught exactly what W3 was written about**: `qbittorrent` published through Authentik with no group policy binding, because `homelab-users` did not exist. Pre-W3 this run exits 0. |
| 78 | **succeeded** | Green after the group was created, and idempotent on the re-run (`changed=1` / `changed=0`). |

**So the ledger is proven, and it earned its keep on its first live run.** W1–W5 collected one
degradation, named the component and the reason, completed all other work, and then refused
to report success.

**Every published app is bound — remediation complete, 2026-08-12.** The nine that were
unbound on 2026-08-11 (`lidarr`, `ntfy`, `observability`, `pbs`, `prowlarr`, `radarr`,
`sonarr`, `uptime-kuma`, `vaultwarden`) were re-deployed; **verified by querying Authentik's
own `policies/bindings/`, not by trusting exit codes** — all ten applications, `qbittorrent`
included, carry exactly one enabled binding to `homelab-users`. The remediation was nine job
runs, no code change, as predicted.

Two rounds ran: executions 80–88 (2026-08-11) and 91–100 (2026-08-12). The second round was
redundant — this table still said the work was open, so a session re-ran it. **A row that
outlives its work costs a live re-run.** Update the row when the run lands, not later.

**The access group is the platform's to create — answered 2026-08-11, `a19105a`.** 403
deliberately removed *directory-content* management from the Authentik role, and that
boundary stands: account names, membership, social login sources and MFA doctrine belong to
the operator. The access group is not content, it is the platform's own ACL primitive, and
the platform that hardcodes `wiring_auth_group: homelab-users` in all twelve app playbooks
has to be the one that creates it. `wiring/authentik.yml` now creates it when absent —
empty, `is_superuser: false`, 400 accepted alongside 201 so concurrent deploys race
harmlessly — and degrades only when the group can neither be found nor created. Membership
stays untouched. Without this, W3 meant every app deploy on a fresh lab failed until an
operator made one group by hand in the UI (execution 77).

| # | Work | Status |
|---|---|---|
| **W1** | **The degradation ledger.** `tasks/report-degradation.yml` appends `{component, reason, fatal}` to `homelabinfra_degradations`; `tasks/assert-no-degradations.yml` fails with the collected list. Collect, then fail — never abort at the first problem. | done |
| **W2** | **Six Uptime Kuma sites** converted — `tasks/wiring/uptime-kuma.yml` (missing credentials, unusable Kuma, missing notification channel) and `roles/uptime-kuma/tasks/main.yml` (key minting, channel provisioning, channel refused). Safe to make fatal: 303 and 404 both closed on live proof these paths work, so they are fallbacks on a working system. | done |
| **W3** | **`wiring/authentik.yml`** — a missing access group is now fatal. It was the worst site on the list: the app was published through Authentik with **no group policy binding**, reachable by every Authentik user, and the run exited 0. **`resolve-media-registry.yml`** — skipped entries are fatal, and its header no longer says "never fatal". | done |
| **W4** | **`vaultwarden-cutover.yml`** — surviving seed files are fatal; the job exists to get secrets off the runner's disk, so reporting success while they remain defeats it. **`wire-media-stack.yml`** — an unreadable `config.xml`. **`rollback-container.yml`** — a container that never answered after rollback. | done |
| **W5** | **The gate is called** as the last task of all 11 app deploy playbooks, `wire-media-stack.yml`, `rollback-container.yml` play 2, and `vaultwarden-cutover.yml`. `wire-media-stack.yml` also pulls play 2's per-host entries out of `hostvars`, and its old `_mw_failed` fail now feeds the same ledger so one message reports everything. | done |
| **W6** | **Document the eight that stay** — a header comment on each saying why degrading is correct, so a future audit does not re-litigate them. All eight now carry a `DEGRADATION BY DESIGN` comment **at the task**, not only in a file header, each naming the reason and saying not to convert it to `report-degradation.yml`. `grep -rn 'DEGRADATION BY DESIGN' ansible/` returns exactly eight; a future audit's first move is that grep. | done |
| **W6.5** | **`tasks/notify.yml`** — the publish call was `failed_when: false` with no `register`, so an undelivered notification was indistinguishable from a delivered one. Every day-2 promise in this repo arrives through it. Now registered and **fatal**: the gate is the last task, so the run completes all its work and then refuses to report green when nobody was told. | done |
| **W7** | **A download-client app playbook** — **qBittorrent**, chosen over SABnzbd because the lab is torrent-side. `playbooks/apps/qbittorrent.yml` + `roles/qbittorrent/` + `vars/app-defaults/qbittorrent.yml`, plus the Semaphore template and Rundeck job. `tasks/app-wiring/arr-download-client.yml` now has something to wire: the `qbittorrent` kind is `auth: userpass`, and the role stores `username`/`password` in `homelab-infra/media/<instance>`, which is exactly where the wiring reads them. Twelve per-app Deploy jobs now. | done |

### The three that were already right

`tasks/app-wiring/arr-download-client.yml`, `bazarr-arr.yml` and `prowlarr-application.yml`
append `state: failed` to `media_wire_results`, and `wire-media-stack.yml` fails on a
non-empty list. That is the collect-then-fail pattern, and it is what W1 generalised.

### The eight that are correct as they are

Five unwiring sites — `unwiring/caddy.yml:53`, `authentik.yml:67`, `guest-record.yml:45`,
`uptime-kuma.yml:51,92`. Removal unwires *before* it stops the app, so that a half-removed
app never serves traffic through a live route. A hard failure there inverts that guarantee,
and did: the 2026-08-02 teardown stranded every removal queued behind one unreachable
provider. A stale route on a proxy that is gone is a smaller problem than a removal that
cannot proceed.

Three that are not failures at all — `remove.yml:153` (host already gone *is* the success
case), `rollback-container.yml:157` (re-pinning a floating tag freezes nothing, and saying
so is more honest than pretending), `record-app-on-guest.yml:89` (a cosmetic Proxmox label).

## Observe if it happens

Not work. Nothing depends on any of it. Do not schedule a session for these; tick one only
if the situation arises on its own.

| Check | Slice | Why it is not work |
|---|---|---|
| An image update triggers a Watchtower Ntfy message with the rollback hint | 201 (closed) | tests Watchtower, not this repo |
| Re-running `Remove App` against a stopped Caddy is a clean no-op | 501 (closed) | the fix is in code and commented; re-observing needs a staged outage |
| Restoring the runner LXC from PBS yields a working runner | 010 | a full DR drill |
| Interrupting bootstrap before cutover is resumable | 014 | fault injection; attempted and refused twice, 2026-08-06 |
| Internal-CA mode; the no-API-provider handoff | 015 | the lab has a real domain on an API provider, so neither path runs here |
| PBS token rotation | — | needs the token deleted PBS-side to force the create path |

## Live slices, and what each is actually waiting on

Every live slice is code-complete and gate-green. None is waiting on code except through
the work queue above.

| Slice | Waiting on |
|---|---|
| 504 wire-media-stack | **one box left** — the adoption PUT, which needs a migration that completes, which is deliberately not wanted yet |
| 505 app-servarr | **one box left** — the same migration. Its `changed=0` re-deploy is done (execution 117, after the API-key fix) |
| 302 | One `oidc` **sign-in** — its object half is done, see the estate section. The unwire-then-denied check. Its catalog-shape idempotency was proven by the 2026-08-12 binding query and its forward_auth redirect by the 2026-08-13 Sonarr sign-in |
| 010, 012, 013, 014 | a bare-metal bootstrap — destroys the lab everything else runs on, so it goes last |
| 011, 300 | nothing; observation only |

## By subject

Slices are cut on the code axis, so one subject spans several.

| Subject | Slices |
|---|---|
| Vaultwarden | app **400** (closed), secret store **014**, token capture **013** |
| Caddy / TLS | wiring **300**, DNS-01 **407** (closed), wildcard bootstrap **015** (closed) |
| Authentik / identity | app **403** (closed), wiring **302**, forward_auth **306** (closed), modes **009** (closed) |
| Observability | app **405** (closed) |
| Config model | provenance **010**, onboarding **012**, estates **008** (closed) |
| Networking | IP allocation **011**, OPNsense **304** (closed) |
| Media | apps **505**, wiring **504** |
| Runners / UI | Rundeck **601** (closed) |
| Day-2 ops | rollback **502** (closed) |

## Standing facts

Recorded because each has cost a session to re-derive. If a row elsewhere contradicts one
of these, that row is stale.

- **The secret store is closed, and so is 016** (2026-08-10, [done/](done/)). Organization-
  scoped Admin ships; Vaultwarden offers no way to make the account collection-scoped.
  **016 was never open on that question** — its last four boxes were Rundeck secret
  handling, all closed by inspection of `rundeck/render-job.py`, which is the single place
  secure options are attached and is why the committed job YAMLs appear to have none.
  **Do not reopen the collection-grant question.**
- **The media stack is wired.** Execution 72, 2026-08-09: four apps resolved, three Prowlarr
  Applications created, all on `media_stack` at 192.168.0.100. Seven defects fixed getting
  there — full account in [505/notes.md](505-app-servarr/notes.md).
- **The platform owns a storage identity** — `homelab-infra`, uid/gid 1313, granted in
  `/etc/subuid`/`/etc/subgid` and mapped into the unprivileged stack host. Verified on
  168000100.
- **Readarr is gone**, by decision 2026-08-09 — retired upstream, and its last build serves
  an unauthenticated UI. **Thirteen per-app Deploy jobs** now (Jellyfin, 2026-08-12), all
  having run green —
  but the eleven ran green *before* W1–W7, on code that could exit 0 without wiring, so that
  is not a claim about the current failure semantics. Only `Deploy qBittorrent` has run
  against them (execution 78).
- **A new job definition is not live until `Reimport Jobs` runs.** Rundeck stores its own
  copy, so `Deploy qBittorrent` was absent from the runner until execution 74 imported it,
  even with the checkout up to date.
- **The media stack can download and play, as of 2026-08-12.** W7 shipped qBittorrent;
  **Jellyfin** shipped the same day and closed the other half — `roles/jellyfin`,
  `playbooks/apps/jellyfin.yml`, both runner surfaces, **thirteen** per-app Deploy jobs.
  Execution 102 was green on its first live run: Jellyfin 10.11.11 on `media_stack`, its
  first-run wizard completed over `/Startup/*` by the deploy, `Movies`/`TV`/`Music` created
  against the same subpaths radarr/sonarr/lidarr write into, admin account in Vaultwarden,
  and an Authentik `homelab-users` binding. Verified in the app and in Authentik, not from
  the exit code. **It deliberately writes no media registry entry** — the registry is how
  `wire-media-stack.yml` finds indexers, clients and *arr apps, W3 made an unplaceable entry
  fatal, and Jellyfin is none of those.
  **SABnzbd closed the usenet gap in code, 2026-08-13** — `roles/sabnzbd`,
  `playbooks/apps/sabnzbd.yml`, `vars/app-defaults/sabnzbd.yml`, both runner surfaces,
  **fourteen** per-app Deploy jobs. **Deployed live 2026-08-13/14** (executions 126–129):
  one transient first-boot timeout, then two clean back-to-back deploys with `changed:
  false` on both API-key tasks — the key held, so the `726f947` defect did not recur.
  **qBittorrent is deployed**
  — execution 78, 2026-08-11, green and idempotent, on `media_stack` with an Authentik group
  binding. Two defects were found and fixed getting there; see the executions table above.
  **It is registered in the *arr apps** — `Wire Media Stack` execution 90, 2026-08-12,
  `failed=0` and `changed=0`, confirming `qbittorrent → sonarr/radarr/lidarr` and all three
  `→ prowlarr indexers`. `changed=0` means execution 79 had already wired it the night
  before, so this only proved the state; the download path is complete end to end.
- **500's one staged import is `apps/nginx.yml`**, which does not exist — 301 shipped the
  wiring pair only.
- **010/012's bootstrap script is the proven path.** It has run from a full wipe to exit 0
  on three consecutive runs, 2026-08-01, and built the current lab.
