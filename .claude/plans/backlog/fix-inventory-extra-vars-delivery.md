# fix-inventory-extra-vars-delivery

**Type:** fix

**Depends on:** fix-inventory-url-and-extra-vars

**Spec:** .claude/specs/config-layering.md; split 2026-07-03 from fix-inventory-url-and-extra-vars
(related: meta 004 owns the api_host/host key rename; meta 002 owns example-file reconciliation)

## Goal

Deliver `homelabinfra_config.proxmox.*` connection details (url, user, token_id, token_secret) to
the `community.proxmox` dynamic inventory plugin so `ansible-inventory -i inventory/proxmox.yml`
works on a fresh clone, without introducing a second place for users to write the Proxmox host.

## Context

Split from fix-inventory-url-and-extra-vars after its round-1 investigation (full evidence in that
plan's Run log, `.claude/plans/done/fix-inventory-url-and-extra-vars.md` once merged) **disproved**
the designed mechanism: with ansible-core 2.18.1 / community.proxmox 2.0.0, `use_extra_vars`
(any section name — its real home is `[inventory_plugins]`, not `[inventory]`) only gates the
`Constructable` mixin's `compose`/`groups`/`keyed_groups` templating. The plugin templates its
connection options directly (`self.templar.template(v)` at `proxmox.py:693`) with a `Templar`
whose `available_variables` is never populated with extra vars. `-e @user-vars.yml` therefore can
never reach `url`/`user`/`token_*`, and `'homelabinfra_config' is undefined` at plugin load is
unfixable via `ansible.cfg`. Proven empirically with an isolated test var, not just by source
reading.

Options observed in that run, not yet chosen (groomer resolves, kicking back anything it cannot):

- **(a) Env-var fallback — recommended.** The plugin documents `PROXMOX_URL` / `PROXMOX_USER` /
  `PROXMOX_TOKEN_ID` / `PROXMOX_TOKEN_SECRET` as option fallbacks. Export them from whatever
  invokes ansible (Rundeck/Semaphore job step, or a small wrapper script) using the same
  `config/proxmox.yml` values. Fits the existing job model (both runners already pass
  `-e @user-vars.yml`; adding env exports to the same step is the smallest surface) and keeps
  `config/proxmox.yml` the single source. Requires deciding what the inventory file's templated
  option lines become (removed so env wins, or env-lookup-backed).
- **(b) Bootstrap-rendered static inventory** — a play templates a concrete inventory file from
  config. More moving parts; drifts from the dynamic-inventory model in CLAUDE.md.
- **(c) Accept as upstream community.proxmox limitation** — leaves the fresh-clone break in
  place; only viable paired with documentation and a tracked upstream issue.

Constraint from the parent item's decisions: `api_host`/`api_port` stay the canonical config keys
(spec config-layering.md); meta 004 owns any key rename, meta 002 owns example reconciliation —
do not pull those in.

## Acceptance criteria

- The differential check from the parent plan flips: `ansible-inventory -i inventory/proxmox.yml`
  with fake creds (`api_host: 127.0.0.1`, fake tokens) fails with a **connection/auth** error
  against `https://127.0.0.1:8006`, not `'homelabinfra_config' is undefined`.
- Users still write the Proxmox host/token in exactly one place (`config/proxmox.yml` or the
  documented runner env vars — not both).
- The chosen mechanism is documented where a fresh-clone user will hit it (inventory file comment
  and/or config.example), and works for both Rundeck and Semaphore job shapes plus bare CLI.
- The `lint` and `test` gates from `.claude/build.yml` show no regression.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
