# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**17 live · 29 archived in [done/](done/) · 3 unreachable in [no-target/](no-target/).**
304 and 502 closed 2026-08-12, both by running things. 306 closed 2026-08-13, same way.

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
| One browser session — Vaultwarden CRUD, Grafana login | **400, 405** | a human at a browser |
| The Rundeck **Config group** — the last never-run job group | **601** outright | two jobs |
| `Deploy SABnzbd`, twice — the second run must produce the same api_key | nothing; proves the last app shipped | two clicks |
| A migration that completes | **504's adoption PUT** — the last box on it | blocked, see below |

**The migration is deliberately not wanted yet** (operator, 2026-08-12). The platform's media
stack mounts `/friends-pool/homelab-media` — empty, 114 G — while the old *arrs use
`/mnt/media`, 40 T. That is intentional: the platform's library is separate, one open slice
is a full teardown and rebuild, and the old apps exist as migration *test material*, not as a
cutover target. Execution 120 stopped exactly there, which is the path guard working. Do not
"fix" the storage mismatch.

008/009/015/407 need a second estate; 010/012/013/014 need a bare-metal bootstrap. Those are
the only genuinely blocked ones now — **304 was never blocked at all**: its "missing"
OPNsense credentials had been in the repo's gitignored `.env` for days.

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
| 601 rundeck-jobs | the Config group, the last never-run jobs |
| 008, 009, 015, 407 | a second estate declared and one app deployed into it |
| 302, 400, 403, 405 | Vaultwarden CRUD, Grafana login, one `oidc` sign-in and 302's unwire-then-denied check. 302's catalog-shape idempotency was proven by the 2026-08-12 binding query, and its forward_auth redirect + 403's admin-login/UI/redirect items by the 2026-08-13 Sonarr sign-in; only `oidc`, the unwire check, 403's registry-token check and its idempotent re-run remain |
| 010, 012, 013, 014 | a bare-metal bootstrap — destroys the lab everything else runs on, so it goes last |
| 011, 300 | nothing; observation only |

## By subject

Slices are cut on the code axis, so one subject spans several.

| Subject | Slices |
|---|---|
| Vaultwarden | app **400**, secret store **014**, token capture **013** |
| Caddy / TLS | wiring **300**, DNS-01 **407**, wildcard bootstrap **015** |
| Authentik / identity | app **403**, wiring **302**, forward_auth **306** (closed), modes **009** |
| Config model | provenance **010**, onboarding **012**, estates **008** |
| Networking | IP allocation **011**, OPNsense **304** (closed) |
| Media | apps **505**, wiring **504** |
| Runners / UI | Rundeck **601** |
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
  **fourteen** per-app Deploy jobs. **It has never been executed, so it is presumed broken**
  until a live `Deploy SABnzbd` says otherwise — both gates passed it, and neither gate
  starts a container. The one thing to watch on that first run is api_key continuity: the
  role resolves the key from `sabnzbd.ini` → the vault item → generate, and seeds it with
  `force: false` before first start, so a second deploy must return the *same* key. A
  different key on run two is the `726f947` defect again. **qBittorrent is deployed**
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
