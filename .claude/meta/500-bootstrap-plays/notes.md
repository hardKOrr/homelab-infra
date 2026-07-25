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

### Gate status

lint clean (production profile); syntax-check clean for bootstrap.yml. test.sh overall
still fails on the pre-existing empty `stacks/rollback-container.yml` stub (slice 502,
untouched — same status as noted in 400).
