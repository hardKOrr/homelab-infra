# 500 — notes

## 2026-07-25 — implementation (built ahead of 401–406, same pattern as 201/202/300)

### Staged-imports decision

`import_playbook` is parsed at load time, so importing a not-yet-written playbook breaks
`--syntax-check` for the whole file. Only `apps/vaultwarden.yml` exists (400); steps 2–7 are
therefore **commented import blocks** with the exact lines each app slice uncomments when its
playbook lands. Each staged block also names the facts play the slice must add
(`generated_facts_service` + Shape B fields per CONTRACT.md §3). 500 stays in-progress until
401–406 flip their blocks on and a live two-pass run passes acceptance.

### What is live now

1. Play 1 (existing load+assert) extended with a provider-typo assert — a misspelled
   provider would otherwise silently skip its conditional bootstrap step.
2. `domain` written to generated facts in Play 1 (wiring reads `homelabinfra_infra.domain`).
3. Vaultwarden import (`instance: vaultwarden`) → facts play (`vaultwarden: {host, port}`,
   read from the `app_deploy` host the import registered) → **admin token gate**.
4. Summary play reports facts.yml top-level keys.

### Two-pass halt mechanics

The gate is a hard `assert` fail, not `meta: end_play` — `end_play` only ends the current
play (later imported plays would still run), and a non-zero rc is what makes
Semaphore/Rundeck show the job as needing attention with the paste-and-re-run instructions
in the output. Token sources checked: `VAULTWARDEN_ADMIN_TOKEN` env var, then
`infrastructure.vaultwarden.admin_token`.

**Acceptance correction:** the README says the two-pass halt is messaged via "Ntfy
notification + console". Console only — Ntfy is step 2 and does not exist when the pass-1
halt fires. Pass 2 has nothing to notify. Treat the Ntfy half of that acceptance line as
void.

### Hazard for 401–406: `app_deploy` group accumulates across imports

Every app playbook's provision play does `add_host: groups: app_deploy`, and add_host
groups persist for the whole `ansible-playbook` run. Chained inside bootstrap, step N's
"Deploy" play (`hosts: app_deploy`) would also hit the hosts steps 1..N-1 added. Options
for the app slices: per-instance group names (`deploy_<instance>`), or a
`group_by`/refresh reset at the top of each provision play. Must be settled in 401 before
its block is uncommented — the Vaultwarden-only pass is unaffected (single member, and the
facts play deliberately reads `groups['app_deploy'][0]` while that is still true).

### Live acceptance TODO

- Fresh Proxmox pass 1: Vaultwarden up, token printed, gate halts with instructions.
- Paste token, pass 2: vaultwarden re-run is a no-op, gate passes, summary lists
  `domain, vaultwarden`.
- Steps 2–7 as their slices land.

### Gate status (first pass, commit f01f1c3)

lint clean (production profile); syntax-check clean for bootstrap.yml. test.sh overall
still fails on the pre-existing empty `stacks/rollback-container.yml` stub (slice 502,
untouched — same status as noted in 400).

## 2026-07-25 — review against the landed Ntfy code (401, working tree)

`playbooks/apps/ntfy.yml` + `roles/ntfy/` now exist. Re-read commit f01f1c3 against them.
Play 1, the Vaultwarden pass, the two-pass gate, and the summary play are all still correct.
Three things must be settled before step 2's staged block is uncommented.

### 1. `app_deploy` accumulation is now a live bug, not a hazard (BLOCKER)

The hazard flagged above is unresolved in 401: `ntfy.yml` uses `groups: app_deploy` in Play 1,
`hosts: app_deploy` in Play 2, and `hostvars[groups['app_deploy'][0]]` in Play 3 — the same
names Vaultwarden uses. Chained under bootstrap, after step 1 the group is
`[vaultwarden, ntfy]`, so uncommenting step 2 as written would:

- run `guest-bootstrap` + `include_role: ntfy` **on the Vaultwarden container** (Play 2), and
- write `notifications.host`/`topic`/credentials from the **Vaultwarden** hostvars, because
  `[0]` is the host step 1 added (Play 3).

Fix in 401 before 500 flips the block: per-instance group `deploy_{{ instance }}` in Play 1's
`add_host`, Play 2's `hosts`, and Play 3's `_ntfy_deploy_host` — applied to `vaultwarden.yml`
too. Bootstrap's "Record Vaultwarden facts" play reads `groups['app_deploy'][0]` and changes
with it (see item 2 — it goes away entirely).

### 2. The step-2 staged comment is stale: apps self-record facts now

`ntfy.yml` Play 3 writes the `notifications` registry key itself (Shape B per CONTRACT.md §3,
with the optional `user`/`password`/`token` fields) before wiring, so a standalone deploy
registers identically to a bootstrap run. The staged block's instruction to "follow it with a
Record Ntfy facts play" would write the key twice.

Convention to adopt: **the app playbook records its own registry key; bootstrap adds no facts
plays.** Update the step 2–7 staged comments accordingly, and move bootstrap's "Record
Vaultwarden facts" play into `vaultwarden.yml` Play 3 (`vaultwarden: {host, port}`) — that also
removes bootstrap's cross-play `groups['app_deploy'][0]` read.

### 3. Ordering gap: the first two guests never get the Ntfy update hook

`guest-bootstrap.yml` is gated on the `homelab_bootstrapped` local fact and calls
`bootstrap/configure-unattended-upgrades.yml`, whose Ntfy notify script/drop-in is conditional
on `homelabinfra_infra.notifications`. Vaultwarden's guest bootstraps at step 1 (no
`notifications` key yet) and Ntfy's own guest bootstraps before its Play 3 writes the key —
so neither gets the hook, and a re-run does not repair it: the marker exists, so the whole
import is skipped. Every app deployed after step 2 is fine.

Options: drop `configure-unattended-upgrades.yml` out from under the marker guard and run it
every deploy (all its tasks are idempotent `copy`/`file`), or add a bootstrap play after step 2
that re-applies it to the two baseline guests. First option is simpler and self-healing for any
app whose deploy predates a later provider change.

### Still valid

- Two-pass token gate (hard `assert`, env var then `infrastructure.vaultwarden.admin_token`).
- Provider-typo assert, `domain` written in Play 1.
- Conditional imports anchored on `hostvars['localhost']` for steps 3–4.
- Console-only halt messaging; the Ntfy half of that acceptance line stays void.

## 2026-07-25 — all three review items resolved; steps 2–7 activated

The review above was written against a partial working tree (401 only). 402–406 have
since landed and all three items are closed. Steps 2–7 of `bootstrap.yml` are now
active imports.

### 1. `app_deploy` accumulation — FIXED (the blocker was real)

Confirmed exactly as described: under `import_playbook`, `add_host` groups persist for
the whole run, so a shared `app_deploy` would have made step N run its role on every
earlier app's guest and read `[0]` — the *first* app's host — when recording facts.

Fixed with the per-instance group the review proposed, applied everywhere:

- `deploy_{{ instance }}` in Play 1's `add_host`, Play 2's `hosts`, and Play 3's
  `_<app>_deploy_host` — in all seven app playbooks and `_template.yml`.
- `tasks/stack/find-or-create-host.yml` gained a `deploy_group` input
  (defaults to `app_deploy` for back-compat); the three Docker playbooks pass
  `deploy_group: "deploy_{{ instance }}"`.
- `hosts:` is templated at **parse** time, so it needs
  `{{ instance | default('_instance_unset') }}` — the same idiom slice 102 applied to
  `restart-app.yml`/`tail-applog.yml`. Without it every app playbook fails
  `--syntax-check` with "'instance' is undefined". Play 1 already asserts `instance`,
  so the default can only fire during a syntax check.
- Rationale is commented at each `hosts:` line and in `playbooks/apps/README.md`, so
  the next app author does not reintroduce a shared group.

### 2. Apps self-record facts — ADOPTED

Convention is now: **the app playbook records its own registry key in Play 3 before
wiring; `bootstrap.yml` adds no facts plays.** All seven do this. Bootstrap's
"Record Vaultwarden facts" play was removed and moved into `apps/vaultwarden.yml`,
which also removed bootstrap's cross-play `groups['app_deploy'][0]` read. The stale
step 2–7 staged comments are gone with the staging.

### 3. Ordering gap on the first two guests — FIXED (option 1)

Took the review's first option: `configure-unattended-upgrades.yml` is no longer
imported from `guest-bootstrap.yml` (which runs once under the marker). Each app
playbook's Play 2 imports it **unconditionally**, right after the guest bootstrap. All
its tasks are idempotent `copy`/`file`, so it is safe every run, and it self-heals any
guest whose deploy predates a later provider change — including the two baseline
guests that bootstrap before Ntfy exists.

### Remaining

- `apps/nginx.yml` import stays commented: slice 301 shipped the nginx *wiring* pair
  but there is no nginx app playbook, and `import_playbook` is parsed at load time.
- Slice 306 (reverse-proxy `forward_auth`) is open and unrelated to bootstrap ordering,
  but until it lands `routing.auth: true` does not enforce anything.

### Gate status (second pass)

lint clean (production profile); syntax-check clean for all 18 playbooks except the
pre-existing empty `stacks/rollback-container.yml` stub (slice 502, untouched).
Still no live run.
