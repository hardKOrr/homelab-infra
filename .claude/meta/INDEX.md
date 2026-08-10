# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**21 live · 25 archived in [done/](done/) · 3 unreachable in [no-target/](no-target/).**

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

The codebase is not generally permissive: 142 `assert` and 11 `fail` sites versus 22
`debug`-on-failure sites, and the 131 `failed_when: false` uses are mostly the
"suppress, then `fail` with a readable message" idiom. This was a bounded defect, not a
culture.

**W1–W5 are done, 2026-08-10.** Both gates green, and the ledger's behaviour is proven by
a scratch play: an empty ledger passes, a non-fatal-only ledger passes, and two fatal
entries fail naming both.

| # | Work | Status |
|---|---|---|
| **W1** | **The degradation ledger.** `tasks/report-degradation.yml` appends `{component, reason, fatal}` to `homelabinfra_degradations`; `tasks/assert-no-degradations.yml` fails with the collected list. Collect, then fail — never abort at the first problem. | done |
| **W2** | **Six Uptime Kuma sites** converted — `tasks/wiring/uptime-kuma.yml` (missing credentials, unusable Kuma, missing notification channel) and `roles/uptime-kuma/tasks/main.yml` (key minting, channel provisioning, channel refused). Safe to make fatal: 303 and 404 both closed on live proof these paths work, so they are fallbacks on a working system. | done |
| **W3** | **`wiring/authentik.yml`** — a missing access group is now fatal. It was the worst site on the list: the app was published through Authentik with **no group policy binding**, reachable by every Authentik user, and the run exited 0. **`resolve-media-registry.yml`** — skipped entries are fatal, and its header no longer says "never fatal". | done |
| **W4** | **`vaultwarden-cutover.yml`** — surviving seed files are fatal; the job exists to get secrets off the runner's disk, so reporting success while they remain defeats it. **`wire-media-stack.yml`** — an unreadable `config.xml`. **`rollback-container.yml`** — a container that never answered after rollback. | done |
| **W5** | **The gate is called** as the last task of all 11 app deploy playbooks, `wire-media-stack.yml`, `rollback-container.yml` play 2, and `vaultwarden-cutover.yml`. `wire-media-stack.yml` also pulls play 2's per-host entries out of `hostvars`, and its old `_mw_failed` fail now feeds the same ledger so one message reports everything. | done |
| **W6** | **Document the eight that stay** — a header comment on each saying why degrading is correct, so a future audit does not re-litigate them. Four already carry the reason; four do not. | **next** |
| **W7** | **A download-client app playbook** (qBittorrent or SABnzbd). `tasks/app-wiring/arr-download-client.yml` is fully implemented and has nothing to wire. Do this before any other new app. | open |

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
| OPNsense host overrides | 304 | blocked on OPNsense API credentials |

## Live slices, and what each is actually waiting on

Every live slice is code-complete and gate-green. None is waiting on code except through
the work queue above.

| Slice | Waiting on |
|---|---|
| 502 rollback-container | one real rollback — its criteria test *this repo's* Compose-rewrite logic, so unlike 201 it is worth running |
| 601 rundeck-jobs | four job definitions never executed: Restart App, Tail App Log, Rollback Container, Check Native Updates |
| 008, 009, 015, 407 | a second estate declared and one app deployed into it |
| 302, 306, 400, 403, 405 | one browser session: Vaultwarden CRUD, Grafana login, Authentik login, one `forward_auth` sign-in |
| 010, 012, 013, 014, 016 | a bare-metal bootstrap — destroys the lab everything else runs on, so it goes last |
| 011, 016, 300, 504, 505 | nothing; observation only |
| 304 | OPNsense API credentials |

## By subject

Slices are cut on the code axis, so one subject spans several.

| Subject | Slices |
|---|---|
| Vaultwarden | app **400**, secret store **014**, token capture **013**, identities **016** |
| Caddy / TLS | wiring **300**, DNS-01 **407**, wildcard bootstrap **015** |
| Authentik / identity | app **403**, wiring **302**, forward_auth **306**, modes **009** |
| Config model | provenance **010**, onboarding **012**, estates **008** |
| Networking | IP allocation **011**, OPNsense **304** |
| Media | apps **505**, wiring **504** |
| Runners / UI | Rundeck **601** |
| Day-2 ops | rollback **502** |

## Standing facts

Recorded because each has cost a session to re-derive. If a row elsewhere contradicts one
of these, that row is stale.

- **The secret store is closed.** Organization-scoped Admin ships, decided 2026-08-09 in
  [016/notes.md](016-vaultwarden-identity-bootstrap/notes.md). Vaultwarden offers no way to
  make the account collection-scoped. Deploy Prowlarr, execution 55, stored nine platform
  items plus Prowlarr's. Secret-storing deploys work.
- **The media stack is wired.** Execution 72, 2026-08-09: four apps resolved, three Prowlarr
  Applications created, all on `media_stack` at 192.168.0.100. Seven defects fixed getting
  there — full account in [505/notes.md](505-app-servarr/notes.md).
- **The platform owns a storage identity** — `homelab-infra`, uid/gid 1313, granted in
  `/etc/subuid`/`/etc/subgid` and mapped into the unprivileged stack host. Verified on
  168000100.
- **Readarr is gone**, by decision 2026-08-09 — retired upstream, and its last build serves
  an unauthenticated UI. Eleven per-app Deploy jobs remain and all eleven have run green.
- **The media stack cannot download or play anything.** `arr-download-client.yml` is fully
  implemented, but no download-client app playbook exists (no qBittorrent, no SABnzbd) and
  there is no media server. This is the largest functional gap in the repo, and it is W7 —
  behind the silent-failure work deliberately, because shipping more apps onto a platform
  that reports success when wiring fails multiplies the problem.
- **500's one staged import is `apps/nginx.yml`**, which does not exist — 301 shipped the
  wiring pair only.
- **010/012's bootstrap script is the proven path.** It has run from a full wipe to exit 0
  on three consecutive runs, 2026-08-01, and built the current lab.
