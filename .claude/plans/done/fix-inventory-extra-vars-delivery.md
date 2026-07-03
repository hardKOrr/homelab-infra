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

Key-shape distinction (established during grooming 2026-07-03): the file whose shape actually
feeds the plugin is the **user-vars file** (`homelabinfra_config.proxmox.api_*`, the shape shown
in `ansible/vars/user-vars-example.yml` and passed as `-e @user-vars.yml` by both runners) — not
`config/proxmox.yml`, which uses a different `host`/`port` key shape whose reconciliation belongs
to meta 002/004. Where this item's Goal and acceptance criteria say "config/proxmox.yml" as the
single source, read that as "the user-vars file" for the inventory path.

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

Deliver the four Proxmox connection values to the inventory plugin through the **process
environment**, not through `-e` extra vars (proven unreachable). Two mechanisms combine:

1. `ansible/inventory/proxmox.yml` reads its `url`/`user`/`token_id`/`token_secret` options from
   `PROXMOX_API_*` env vars via `lookup('env', ...)`. This is provable from the parent run's own
   evidence: `proxmox.py:693` calls `self.templar.template(v)` on each option value, and a
   `lookup('env', 'X')` inside that value evaluates controller-side with no dependency on
   `available_variables` — the exact reason bare `homelabinfra_config.*` failed does **not** apply
   to an env lookup.
2. A committed wrapper `ansible/scripts/with-proxmox-env.sh` reads those values out of the same
   user-vars file the playbook already consumes (`homelabinfra_config.proxmox.api_*`) and exports
   them before `exec`-ing the ansible command — so the bare-CLI / fresh-clone user still writes the
   host/token in exactly one place. Rundeck/Semaphore export the same vars from their own secret
   stores in the job step (documented, since no job YAML exists yet).

Work test-first: apply the inventory edit, run the differential `ansible-inventory` check in
Verification until it flips from `'homelabinfra_config' is undefined` to a connection error against
`https://127.0.0.1:8006`, then add the wrapper and prove it produces the same flip, then confirm the
`lint`/`test` gates are unchanged.

### Step 1 — `ansible/inventory/proxmox.yml` (edit, exists)

Replace the four templated connection options and the two stale comments. Keep the schemed-URL
derivation (parent decision) but source it from env. Exact target for the top of the file:

```yaml
---
# community.proxmox templates its connection options (url/user/token_*) with a Templar that never
# receives -e extra vars (proven: .claude/plans/done/fix-inventory-url-and-extra-vars.md Run log),
# so these options read PROXMOX_API_* from the process environment via lookup('env', ...), which the
# same Templar DOES evaluate (no host/extra vars needed). Populate them with
# scripts/with-proxmox-env.sh (bare CLI / fresh clone) or export them in the Rundeck/Semaphore job
# step. Playbooks still receive homelabinfra_config via -e @user-vars.yml for task-time vars; only
# this inventory file is env-driven.
plugin: community.proxmox.proxmox
# Derive a full URL (scheme + host + port) from the same bare host the provisioning modules consume,
# so users configure the host in exactly one place. community.proxmox's inventory `url` option
# expects a scheme (its default is http://localhost:8006); port falls back to 8006.
url: "https://{{ lookup('env', 'PROXMOX_API_HOST') }}:{{ lookup('env', 'PROXMOX_API_PORT') | default('8006', true) }}"
user: "{{ lookup('env', 'PROXMOX_API_USER') | default('root@pam', true) }}"
token_id: "{{ lookup('env', 'PROXMOX_API_TOKEN_ID') }}"
token_secret: "{{ lookup('env', 'PROXMOX_API_TOKEN_SECRET') }}"
```

Leave every line from `validate_certs: false` downward (the `validate_certs` comment, `want_facts`,
`groups`, `keyed_groups`) byte-for-byte unchanged. Do **not** rename any config key (meta 004) or
touch example files beyond Step 4 (meta 002).

The `default('8006', true)` / `default('root@pam', true)` two-arg form applies the default when the
env lookup returns an empty string (unset var), matching the `api_port: 8006` / `api_user: root@pam`
defaults in `ansible/vars/homelabinfra-defaults.yml`. `token_id`/`token_secret` have no default —
they are required.

### Step 2 — `ansible/scripts/with-proxmox-env.sh` (create, new)

New directory `ansible/scripts/`. The script parses the user-vars file with the interpreter ansible
already depends on (PyYAML), exports `PROXMOX_API_*`, then execs the passed command. Pseudo-code:

```bash
#!/usr/bin/env bash
# with-proxmox-env.sh — export the community.proxmox inventory plugin's PROXMOX_API_* connection
# environment from a homelab-infra user-vars file, then exec the given ansible command.
#
# Why: community.proxmox 2.0.0's inventory plugin cannot receive -e extra vars in its connection
# options (see .claude/plans/done/fix-inventory-url-and-extra-vars.md). inventory/proxmox.yml reads
# PROXMOX_API_* via lookup('env', ...) instead; this wrapper fills them from the same user-vars file
# -e @<file> feeds the playbook, keeping the Proxmox host/token in one place.
#
# Usage:  with-proxmox-env.sh <user-vars.yml> <ansible-command> [args...]
# Example (from ansible/):
#   bash scripts/with-proxmox-env.sh vars/user-vars.yml \
#     ansible-inventory -i inventory/proxmox.yml --list
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <user-vars.yml> <ansible-command> [args...]" >&2
  exit 2
fi

vars_file="$1"; shift
[ -f "$vars_file" ] || { echo "ERROR: user-vars file not found: $vars_file" >&2; exit 1; }

# Emit `export KEY=VALUE` lines; fail loudly if the proxmox block or a required key is absent.
env_exports="$(python3 - "$vars_file" <<'PY'
import sys, yaml
with open(sys.argv[1]) as fh:
    data = yaml.safe_load(fh) or {}
prox = ((data.get("homelabinfra_config") or {}).get("proxmox") or {})
missing = [k for k in ("api_host", "api_token_id", "api_token_secret") if not prox.get(k)]
if missing:
    sys.stderr.write("ERROR: %s missing proxmox key(s): %s\n" % (sys.argv[1], ", ".join(missing)))
    sys.exit(1)
def q(v):  # single-quote-safe shell literal
    return "'" + str(v).replace("'", "'\"'\"'") + "'"
print("export PROXMOX_API_HOST=%s" % q(prox["api_host"]))
print("export PROXMOX_API_PORT=%s" % q(prox.get("api_port") or 8006))
print("export PROXMOX_API_USER=%s" % q(prox.get("api_user") or "root@pam"))
print("export PROXMOX_API_TOKEN_ID=%s" % q(prox["api_token_id"]))
print("export PROXMOX_API_TOKEN_SECRET=%s" % q(prox["api_token_secret"]))
PY
)" || { echo "ERROR: failed to parse Proxmox connection from $vars_file" >&2; exit 1; }

eval "$env_exports"
exec "$@"
```

Notes for the implementer:
- The `env_exports="$(...)" || { ...; exit 1; }` form is required: a plain assignment does **not**
  trip `set -e` on the substitution's failure, so the explicit `||` propagates the python exit.
- `q()` single-quotes each value so token secrets with shell metacharacters survive `eval`.
- Do not have the wrapper append `-e @<file>` itself — it is single-purpose (env export). The caller
  still passes `-e @<user-vars.yml>` for playbook task vars.

### Step 3 — `.gitattributes` (edit, exists)

Add an LF-eol rule so the new script's shebang never breaks under WSL bash (same reason the gate
scripts have one). Append:

```
# Runtime wrapper runs inside WSL/Linux bash — force LF so the shebang never breaks.
ansible/scripts/*.sh text eol=lf
```

### Step 4 — documentation where a fresh-clone user hits it (edits, all exist)

Document the env-var mechanism in the files a fresh-clone user actually opens. Do **not** touch
`config.example/proxmox.yml` (different `host`/`port` key shape — meta 002/004 scope; commenting it
would teach the wrong shape):

1. `ansible/vars/user-vars-example.yml` — add a comment above the `proxmox:` block noting the
   inventory plugin reads these values from `PROXMOX_API_*` env vars, populated by
   `scripts/with-proxmox-env.sh` (bare CLI) or the job runner. This is the file whose key shape the
   inventory actually consumes.
2. `rundeck/README.md` — under "Key Variables", state that the Ansible job step must export
   `PROXMOX_API_HOST` / `PROXMOX_API_PORT` / `PROXMOX_API_USER` / `PROXMOX_API_TOKEN_ID` /
   `PROXMOX_API_TOKEN_SECRET` (secret sourced from `keys/proxmox/api-token`) before invoking
   ansible, or wrap the invocation in `ansible/scripts/with-proxmox-env.sh`.
3. `semaphore/README.md` — same note under "Environment Variables": export the five `PROXMOX_API_*`
   vars from Semaphore secrets, or use the wrapper.

### Files touched

- `ansible/inventory/proxmox.yml` (edit) — env-driven connection options + updated comments.
- `ansible/scripts/with-proxmox-env.sh` (create) — user-vars → `PROXMOX_API_*` env exporter.
- `.gitattributes` (edit) — LF rule for `ansible/scripts/*.sh`.
- `ansible/vars/user-vars-example.yml` (edit) — one comment block.
- `rundeck/README.md`, `semaphore/README.md` (edit) — env-var / wrapper note.

Nothing else. No key rename (meta 004), no `config.example/` reconciliation (meta 002), no
`ansible.cfg` change (proven ineffective in the parent run), no playbook/task/role change.

## Decisions

- **Mechanism → option (a), env-var delivery — confirmed viable, not kicked back.** The plugin
  templates its connection options controller-side (`proxmox.py:693`); an env value reaches them
  either via the plugin's documented fallback or via `lookup('env')`. No repo evidence blocks it;
  it fits the existing runner model (job step exports env) and keeps a single source. Rejected (b)
  bootstrap-rendered static inventory (drifts from the dynamic-inventory model in CLAUDE.md, more
  moving parts) and (c) accept-as-limitation (leaves the fresh-clone break).
- **Sub-decision: templated option lines → replace with `lookup('env', ...)`, not remove.** Both
  paths were offered. `lookup('env')` is provable from evidence already captured (the Templar
  evaluates the option value; an env lookup needs no `available_variables`), whereas *removing* the
  lines to let the native fallback win relies on the plugin declaring `env:` fallbacks in its
  argspec — asserted in the dossier but never verified at source in the parent run. Choosing the
  lookup eliminates that residual `[unverified]` and keeps the schemed-URL derivation visible in the
  inventory file (parent's reviewed decision preserved).
- **Env var names → custom `PROXMOX_API_*`, not the plugin's documented `PROXMOX_URL`/`PROXMOX_USER`
  /`PROXMOX_TOKEN_ID`/`PROXMOX_TOKEN_SECRET`.** Because we set the options explicitly via `lookup`
  (not the native fallback), the documented names carry no benefit and would collide with the native
  fallback if it exists (our explicit `""` on an unset var would override it). `PROXMOX_API_*` maps
  1:1 onto the canonical `api_host`/`api_port`/`api_user`/`api_token_id`/`api_token_secret` config
  keys, making the wrapper's mapping obvious and self-documenting.
- **URL derivation stays in the inventory (`https://{host}:{port}`), env carries the bare host.**
  Keeps the parent item's reviewed scheme+port derivation and its comment; the wrapper exports the
  bare `api_host`/`api_port` rather than pre-building a URL, so there is one derivation site.
- **Wrapper reads the user-vars file, not `config/proxmox.yml`.** The inventory consumes
  `homelabinfra_config.proxmox.api_*` (the `ansible/vars/user-vars-example.yml` shape), which is what
  `-e @user-vars.yml` already feeds. `config/proxmox.yml` uses a different `host`/`port` shape whose
  reconciliation is meta 002/004. Sourcing the wrapper from the user-vars file keeps a genuine single
  source for the inventory path *and* stays out of the key-rename scope. (See thin-projection note in
  the groomer report — the dossier says "config/proxmox.yml" but the plumbing that reaches the plugin
  is the user-vars file.)
- **Wrapper parses YAML with `python3`/PyYAML, not shell text-munging.** ansible depends on PyYAML
  everywhere it runs, so `python3 -c` is available and robust; a bash YAML parser would be fragile.
  The script is committed with LF (`.gitattributes`), matching the established gate-script pattern.
- **Runner support → documentation only, no job YAML.** `rundeck/jobs/*.yaml` and
  `semaphore/project.json` do not exist yet (both READMEs mark them TODO). The deliverable for the
  runner shapes is the README note plus the reusable wrapper; authoring the job files is separate
  future work.
- **`config.example/proxmox.yml` deliberately not touched.** It uses the `host`/`port` key shape
  (meta 002/004). Adding an env-var note there would document a key shape the inventory does not
  consume. Fresh-clone documentation lives in `user-vars-example.yml` (correct shape) and the
  inventory file itself instead.
- **`ansible.cfg` unchanged.** The parent run proved no `[inventory]`/`[inventory_plugins]` setting
  delivers extra vars to this plugin's connection options; it stays byte-identical to master.

## Verification

### Implementer proves (test-first)

1. **Differential inventory check — the load-bearing proof.** The `lint`/`test` gates neutralise the
   dynamic inventory (`ANSIBLE_INVENTORY=localhost,`), so prove the fix directly. From `ansible/` in
   WSL, using the gate venv (which the parent run already fitted with `requests`/`proxmoxer` — a
   local venv prereq, not a repo change):

   ```bash
   cd /mnt/c/Users/korr/source/repos/homelab-infra/ansible
   PROXMOX_API_HOST=127.0.0.1 PROXMOX_API_TOKEN_ID=fake PROXMOX_API_TOKEN_SECRET=fake \
   ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg \
     "$HOME/.venvs/homelab-ansible/bin/ansible-inventory" -i inventory/proxmox.yml --list
   ```

   - **Expected WITH the fix:** templating succeeds and the plugin attempts to reach
     `https://127.0.0.1:8006`, failing with a **connection/refused/auth** error — not
     `'homelabinfra_config' is undefined`. Note no `-e @` is passed: the env vars alone drive the
     inventory. Capture the error text in the Run log.
   - **Negative control:** re-run with the three env vars unset. Expected: still no
     undefined-variable error (url renders to `https://:8006`, a connection error), proving the old
     `'homelabinfra_config' is undefined` failure is genuinely gone, not merely masked.

2. **Wrapper check — proves the single-source path.** Write a throwaway user-vars file to the
   scratchpad (no real secrets):

   ```yaml
   homelabinfra_config:
     proxmox:
       api_host: "127.0.0.1"
       api_token_id: "fake"
       api_token_secret: "fake"
   ```

   ```bash
   cd /mnt/c/Users/korr/source/repos/homelab-infra/ansible
   ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg \
     bash scripts/with-proxmox-env.sh /tmp/fake-user-vars.yml \
     "$HOME/.venvs/homelab-ansible/bin/ansible-inventory" -i inventory/proxmox.yml --list
   ```

   Expected: the same connection error against `https://127.0.0.1:8006` as step 1 — proves the
   wrapper reads the user-vars file and exports `PROXMOX_API_*` correctly. Also confirm the
   missing-key guard: run it against a vars file with `proxmox: {}` and expect a non-zero exit with
   the `missing proxmox key(s)` message, no ansible invocation.

3. **`lint` gate — no regression.** Run the exact build.yml command:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'` →
   expect exit 0, `Passed: 0 failure(s), 0 warning(s) on 62 files`. The lint target is
   `playbooks roles tasks vars`, so neither `inventory/proxmox.yml` nor `scripts/` is scanned; this
   is a non-regression check (the added script is shell, not an ansible artifact).

4. **`test` gate — unchanged.** Run
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'` →
   expect exit 1 with exactly the three pre-existing known-defect playbooks failing
   (`maintenance/restart-app.yml`, `maintenance/tail-applog.yml` — meta 102;
   `stacks/rollback-container.yml` — meta 502). This item touches no playbook, so nothing else moves.

5. **YAML/shell validity.** The step-1 run parses `inventory/proxmox.yml`; a clean gate run parses
   nothing new; `bash -n scripts/with-proxmox-env.sh` reports no syntax error.

### korr-qa senior pass confirms

- `ansible/inventory/proxmox.yml`: `url`/`user`/`token_id`/`token_secret` read from `PROXMOX_API_*`
  via `lookup('env', ...)`; `url` keeps the `https://host:port` derivation with `default('8006',
  true)`; no `homelabinfra_config` reference remains; no config key renamed; every line from
  `validate_certs:` down is byte-for-byte unchanged.
- The differential `ansible-inventory` transcript (with and via the wrapper) shows the
  connection-error / no-undefined-variable flip, independently reproducible.
- `ansible/scripts/with-proxmox-env.sh` exists, is LF-terminated (via `.gitattributes`), exports the
  five `PROXMOX_API_*` vars from the user-vars file, fails loudly on a missing required key, and
  hardcodes no secrets.
- The fresh-clone mechanism is documented in `ansible/vars/user-vars-example.yml`,
  `rundeck/README.md`, and `semaphore/README.md`; `config.example/proxmox.yml` is untouched.
- `git diff` touches only the six files named under "Files touched" — no key rename, no
  `config.example/` change, no `ansible.cfg` change, no playbook/task/role change (meta 002/004 scope
  intact).
- `lint` exits 0 (62 files) and `test` exits 1 on only the three documented known-defect playbooks.

## Run log

### [implementer] 2026-07-03

Branch `fix/fix-inventory-extra-vars-delivery` off `master` (d0bcec6). Implemented Steps 1-4
exactly as specified; no scope beyond the plan's "Files touched" list.

**Changes:**
- `ansible/inventory/proxmox.yml` — replaced the four `homelabinfra_config.proxmox.*`-templated
  connection options with `lookup('env', 'PROXMOX_API_*')` equivalents (URL derivation and
  `default(..., true)` two-arg form preserved per plan); replaced the stale extra-vars comment with
  the env-lookup rationale comment from the plan. Everything from `validate_certs:` down is
  byte-for-byte unchanged (verified by diff).
- `ansible/scripts/with-proxmox-env.sh` (new) — reads `homelabinfra_config.proxmox.api_*` from a
  user-vars file via PyYAML, exports the five `PROXMOX_API_*` vars, execs the passed command. Fails
  loudly (exit 1, `missing proxmox key(s)` message) on a missing required key, per the plan's
  pseudo-code verbatim.
- `.gitattributes` — added `ansible/scripts/*.sh text eol=lf`.
- `ansible/vars/user-vars-example.yml` — comment above the `proxmox:` block pointing to the
  `PROXMOX_API_*` env mechanism and the wrapper.
- `rundeck/README.md`, `semaphore/README.md` — added the env-var / wrapper note under each file's
  existing "Key Variables" / "Environment Variables" section.

`config.example/proxmox.yml`, `ansible.cfg`, and no config key were touched (meta 002/004 scope
preserved). `git status --porcelain` shows exactly these five tracked-file edits plus the new
`ansible/scripts/` directory as changes attributable to this plan (the deleted
`.claude/plans/backlog/fix-inventory-extra-vars-delivery.md` and modified
`.claude/settings.local.json` predate this round — present in the working tree before this branch
was created, per the session's starting `git status`).

**Verification (all commands run via `wsl bash -lc ...` from repo root or `ansible/`, using
`$HOME/.venvs/homelab-ansible`):**

1. Differential inventory check (load-bearing) —
   `PROXMOX_API_HOST=127.0.0.1 PROXMOX_API_TOKEN_ID=fake PROXMOX_API_TOKEN_SECRET=fake ANSIBLE_CONFIG=.../ansible.cfg ansible-inventory -i inventory/proxmox.yml --list`
   → `HTTPSConnectionPool(host='127.0.0.1', port=8006): ... Connection refused` — a connection
   error against `https://127.0.0.1:8006`, not `'homelabinfra_config' is undefined`. **Flip
   confirmed.**
   Negative control (env vars unset): `Invalid URL 'https://:8006/api2/json/nodes': No host
   supplied` — still no undefined-variable error, confirming the old failure mode is gone, not
   masked.

2. Wrapper check — wrote a throwaway user-vars file to the scratchpad
   (`api_host: 127.0.0.1`, `api_token_id/secret: fake`) and ran
   `bash scripts/with-proxmox-env.sh <file> ansible-inventory -i inventory/proxmox.yml --list` →
   same `Connection refused` against `https://127.0.0.1:8006` as step 1. **Single-source path
   proven.**
   Missing-key guard: ran the wrapper against `proxmox: {}` → exit code 1 (confirmed via a
   script-file invocation to sidestep the Windows-shell-relay `$?` artifact noted in
   `.claude/build.yml`), stderr `... missing proxmox key(s): api_host, api_token_id,
   api_token_secret` / `ERROR: failed to parse Proxmox connection from ...`, and no ansible
   invocation occurred (echo argument never printed).

3. `lint` gate: `wsl bash -lc 'cd .../homelab-infra && bash .claude/gate/lint.sh'` → exit 0,
   `Passed: 0 failure(s), 0 warning(s) on 62 files. Profile 'min' was required, but 'production'
   profile passed.` Matches expected — no regression.

4. `test` gate: `wsl bash -lc 'cd .../homelab-infra && bash .claude/gate/test.sh'` → exit 1
   (confirmed via script-file invocation), `Found 11 playbook(s) to syntax-check.`, failures on
   exactly `playbooks/maintenance/restart-app.yml` (`'instance' is undefined`),
   `playbooks/maintenance/tail-applog.yml` (`'instance' is undefined`), and
   `playbooks/stacks/rollback-container.yml` (`Empty playbook, nothing to do`) — the three
   documented known-defect playbooks (meta 102, meta 502), nothing else moved.

5. YAML/shell validity: step 1's ansible-inventory run parses `inventory/proxmox.yml` cleanly
   (fails only on the network call, not YAML/templating); `bash -n
   ansible/scripts/with-proxmox-env.sh` → exit 0, no syntax error (confirmed via script-file
   invocation).

**Note on evidence-capture method:** several inline `wsl bash -lc "...; echo RC=\$?"` one-liners
showed `RC=0` even when the underlying command demonstrably exited non-zero (confirmed by rerunning
the identical logic as a `.sh` file invoked via `wsl bash <path>`), matching the shell-relay
quoting hazard `.claude/build.yml` already documents for this environment. All exit-code claims
above (wrapper guard exit 1, test gate exit 1, `bash -n` exit 0) were reproduced with the
script-file method before being reported; the gate commands themselves (steps 3-4) were also
re-run this way to be sure their reported RCs are trustworthy.

No decisions needed — the plan's Decisions section resolved the mechanism, env-var names, URL
derivation site, and wrapper source file; nothing encountered during implementation contradicted
those choices. No blockers.

Working tree left uncommitted per protocol.

### [reviewer] 2026-07-03

[reviewer] verdict: PASS

Independently reproduced all implementer evidence via script-file invocation (`wsl bash <path>`,
avoiding the inline-`echo $?` relay hazard):
- `lint` gate: exit 0, "Passed: 0 failure(s), 0 warning(s) on 62 files" — matches.
- `test` gate: exit 1, failures on exactly `restart-app.yml`, `tail-applog.yml`,
  `rollback-container.yml` (the three documented known-defect playbooks) — matches, no regression.
- Differential `ansible-inventory -i inventory/proxmox.yml --list` with `PROXMOX_API_*` env vars set
  and no `-e`: connection error `HTTPSConnectionPool(host='127.0.0.1', port=8006): ... Connection
  refused`, not `'homelabinfra_config' is undefined` — flip confirmed. Negative control (env unset):
  `Invalid URL 'https://:8006/api2/json/nodes': No host supplied` — old failure mode genuinely gone.
  (Note: `ansible-inventory` itself exits 0 in both cases — the plugin's `auto` loader catches the
  connection error as a WARNING and falls back to an empty localhost-only inventory rather than
  hard-failing. This matches the plan's Verification step, which specifies checking the error text,
  not the process exit code, so it is not a gap against the acceptance criteria as written.)
- Wrapper (`scripts/with-proxmox-env.sh`) against a valid throwaway user-vars file: same
  connection-refused error. Missing-key guard against `proxmox: {}`: exit 1, `missing proxmox
  key(s): api_host, api_token_id, api_token_secret`, no ansible invocation. `bash -n`: exit 0.

Diff review against the plan:
- `ansible/inventory/proxmox.yml`: all four options now `lookup('env', 'PROXMOX_API_*')`, no
  `homelabinfra_config` reference remains, URL derivation and `default(..., true)` two-arg form
  preserved. Everything from `validate_certs:` down is byte-for-byte identical to master (diffed
  directly, confirmed).
- `ansible/scripts/with-proxmox-env.sh`: matches the plan's pseudo-code, no hardcoded secrets,
  fails loudly on missing keys, `eval`+single-quote escaping is sound.
- `.gitattributes`, `ansible/vars/user-vars-example.yml`, `rundeck/README.md`,
  `semaphore/README.md`: documentation additions match the plan's intent; `config.example/proxmox.yml`
  and `ansible.cfg` untouched.
- `git status`/`git diff master --stat` show exactly the six named files touched (plus the
  pre-existing, out-of-scope `.claude/plans/backlog/...` deletion and `.claude/settings.local.json`
  change that predate this branch, per the implementer's note — verified against the session's
  starting `git status`).
- No secrets committed, no injection surface (wrapper uses `eval` only on values it itself
  single-quote-escapes from trusted local YAML, not untrusted input), no swallowed errors (wrapper
  uses `set -euo pipefail` plus explicit `||` on the command-substitution assignment as the plan's
  notes require).

No findings. Gate evidence is green and independently reproduced, diff matches the plan exactly,
reflexes clear.

### [qa] 2026-07-03

[qa] verdict: PASS

Senior pass over the diff and both rounds. Confirmed directly: all four inventory connection
options env-driven via `lookup('env', 'PROXMOX_API_*')` with the schemed-URL derivation and
two-arg `default(..., true)` preserved; no `homelabinfra_config` reference remains;
`validate_certs:` downward byte-identical; wrapper matches the plan's pseudo-code (single-quote
escaping, `||` on the substitution assignment, no secrets) and is LF-only on disk; docs landed in
`user-vars-example.yml` + both runner READMEs; `config.example/proxmox.yml` and `ansible.cfg`
untouched. Exactly the six named files in the diff. Reviewer's exit-0-on-connection-error note
accepted: the acceptance criterion is the error-text flip, which both rounds reproduced
independently. The pre-existing `.claude/plans/backlog/<slug>.md` deletion and
`.claude/settings.local.json` edit predate this branch and stay out of the commit (workspace
state, committed separately per the repo's pattern). No decisions were flagged; nothing to
resolve. Clear to commit.
