# implement-config-loader

**Type:** feature

**Depends on:** variable-loading-contract (meta slice 000 — `ansible/vars/CONTRACT.md`)

**Spec:** `ansible/vars/CONTRACT.md` §2 (load map), §4 (merge order), §5 (required/optional keys); `.claude/specs/config-layering.md`; `.claude/specs/namespace-merge-discipline.md`

## Goal

Extend `ansible/tasks/load-user-vars.yml` so it loads `config/proxmox.yml` and `config/infrastructure.yml` into `homelabinfra_config` per the merge order in the variable-loading contract, making the documented "copy `config.example/*.yml` → `config/`, run bootstrap" workflow actually populate the keys the code reads. Today the loader knows only the legacy single-file `user_vars_file` path.

## Context

`ansible/tasks/load-user-vars.yml` (14 lines) currently: asserts `user_vars_file is defined or homelabinfra_config is defined`, includes `vars/homelabinfra-defaults.yml` (unwrapping the `homelabinfra_defaults:` key), optionally includes `user_vars_file`, then merges `homelabinfra_defaults | combine(homelabinfra_config)`. Nothing reads `config/proxmox.yml` or `config/infrastructure.yml`, yet `bootstrap.yml` and every app playbook call this task then read `homelabinfra_config.proxmox.*` / `homelabinfra_config.infrastructure.*`. The documented workflow therefore fails on any realistic input.

**The contract is the source of truth (`ansible/vars/CONTRACT.md`).** This slice implements §2/§4/§5 for the two config files only:

- **`config/proxmox.yml`** — no wrapper in the file; top-level keys are `proxmox:`, `networks:`, `ansible:`. The loader injects those keys directly into `homelabinfra_config` (they land at the top, keeping the file human-readable). Contract §2 row 2.
- **`config/infrastructure.yml`** — no wrapper; top-level keys are `domain:`, `reverse_proxy:`, `sso:`, `notifications:`, `dns:`, `backups:`, `vaultwarden:`. The loader wraps the whole file under `homelabinfra_config.infrastructure` — this matches what `bootstrap.yml` asserts. Contract §2 row 3.
- **Merge order (§4, low → high):** (1) `homelabinfra-defaults.yml` seed → (2) `config/proxmox.yml` → (3) `config/infrastructure.yml` under `.infrastructure` → (4) `user_vars_file` back-compat (already self-wrapping). All merges `combine(recursive=True)`, later layers win per key (`.claude/specs/namespace-merge-discipline.md`).
- **Missing files must not error.** `config/` is gitignored and absent on a clean checkout; a fresh clone has no `config/proxmox.yml` yet. Use `failed_when: false` on the two config includes. Required-key validation is the caller's job (`bootstrap.yml` and app playbooks already assert `homelabinfra_config.proxmox.*` / `.infrastructure.*`), not this loader's — the loader stays permissive so partial/absent config surfaces as a friendly caller-side assert, not an `include_vars` file-not-found crash.

**Two projected hazards the groomer must resolve — do not copy the meta README's snippet verbatim:**

1. **Config-file path is caller-depth-sensitive.** `load-user-vars.yml` is `import_tasks`-ed from playbooks at two different directory depths: `playbooks/bootstrap.yml` (playbook_dir = `ansible/playbooks`) and `playbooks/apps/*.yml`, `playbooks/proxmox/*.yml`, `playbooks/docker/*.yml`, `playbooks/maintenance/*.yml` (playbook_dir = `ansible/playbooks/<subdir>`). The meta README wrote `{{ playbook_dir }}/../../config/proxmox.yml`, but a **fixed `../` count off `playbook_dir` cannot be correct for both depths**: `_template.yml:42` already uses **three** dots (`{{ playbook_dir }}/../../../config/apps/{{ instance }}.yml`) precisely because it sits one level deeper than bootstrap. The existing defaults include in this same task file uses a plain relative `../../vars/homelabinfra-defaults.yml` and resolves correctly from **all** callers today — the config includes must resolve correctly from all callers the same way. The groomer must pick a path expression that is stable regardless of which playbook imported the task (matching the existing defaults line's resolution behavior, not the README's `playbook_dir` snippet), and the implementer must confirm it resolves from both a `playbooks/`-level and a `playbooks/apps/`-level caller. If the correct expression cannot be settled by inspection, kick back `NEEDS HUMAN`.

2. **The pre-flight assert blocks the new primary path.** The existing first task asserts `user_vars_file is defined or homelabinfra_config is defined`. Under the documented workflow a user has *neither* (config lives in `config/*.yml`, not in a pre-set `homelabinfra_config` var), so this assert fails the very path this slice enables. It must be relaxed so config-file-only input passes, while still keeping caller-side required-key asserts as the real validation gate. Resolve how (relax vs. remove) in `## Decisions`.

**Out of scope — owned by sibling slices, do not touch here:**
- Proxmox key naming `host`/`port` vs canonical `api_host`/`api_port` → **slice 004**. This loader must merge whatever keys the file carries as-is; it does not rename.
- Reconciling `config.example/*.yml` unwrapped keys to match the namespaces → **slice 002**.
- `config/apps/<instance>.yml` schema and the per-app `app_config` merge → **slice 005** (that merge is separate from `homelabinfra_config`; Contract "App-level layering note").
- `config/.generated/facts.yml` → `homelabinfra_infra` loading and shape → already done in `_template.yml:47`; not this task.

The legacy `user_vars_file` path (last-wins, highest precedence) must keep working so existing tests using `vars/user-vars-example.yml` pass unchanged until migrated.

## Acceptance criteria

- `ansible/tasks/load-user-vars.yml` includes `config/proxmox.yml` and `config/infrastructure.yml` when present, each with `failed_when: false` so a missing file does not error.
- `config/proxmox.yml` keys merge into `homelabinfra_config` at top level (`homelabinfra_config.proxmox.*`, `.networks.*`, `.ansible.*`); `config/infrastructure.yml` merges under `homelabinfra_config.infrastructure` (`homelabinfra_config.infrastructure.domain`, `.reverse_proxy.*`, …) — verifiable from the diff against Contract §2/§4.
- Merge order is defaults → proxmox → infrastructure → `user_vars_file`, all via `combine(recursive=True)` (no bare `set_fact` dict replacement — `.claude/specs/namespace-merge-discipline.md`).
- The config-file include paths resolve correctly whether the task is imported from a `playbooks/`-level playbook (bootstrap) or a `playbooks/<subdir>/`-level playbook (apps) — not a fixed-`../`-count `playbook_dir` expression that only fits one depth.
- The pre-flight assert no longer fails config-file-only input (neither `user_vars_file` nor a pre-set `homelabinfra_config`).
- Existing `user_vars_file` callers still work: the legacy path remains and wins on conflict.
- The `lint` and `test` gates in `.claude/build.yml` pass.

## Plan

**Single change, single file touched:** `ansible/tasks/load-user-vars.yml`. No other file is edited
(the callers already read `homelabinfra_config.proxmox.*` / `.infrastructure.*`; this slice only makes
the loader populate them). Replace the entire file body with the four-layer loader below.

**Exact final content of `ansible/tasks/load-user-vars.yml`** (implement verbatim; it is the contract):

```yaml
---
# Loads the homelabinfra_config input layer per ansible/vars/CONTRACT.md §2/§4.
# Merge order (low -> high precedence):
#   1. vars/homelabinfra-defaults.yml   (seed; unwrap `homelabinfra_defaults:`)
#   2. config/proxmox.yml               (top-level proxmox/networks/ansible keys, injected as-is)
#   3. config/infrastructure.yml        (whole file wrapped under .infrastructure)
#   4. user_vars_file / pre-set homelabinfra_config  (legacy back-compat; wins on conflict)
# config/ is gitignored and may be absent on a clean checkout, so the two config includes use
# `failed_when: false` and default to {} in the merge. Required-key validation is the CALLER's job
# (bootstrap.yml / app playbooks assert homelabinfra_config.proxmox.* / .infrastructure.*), not this
# loader's — the loader stays permissive so partial/absent config surfaces as a friendly caller-side
# assert, never an include_vars file-not-found crash.

- name: Load homelabinfra defaults
  ansible.builtin.include_vars:
    file: ../../vars/homelabinfra-defaults.yml

- name: Load config/proxmox.yml if present (absent on a clean checkout)
  ansible.builtin.include_vars:
    file: ../../../config/proxmox.yml
    name: _config_proxmox
  failed_when: false

- name: Load config/infrastructure.yml if present (absent on a clean checkout)
  ansible.builtin.include_vars:
    file: ../../../config/infrastructure.yml
    name: _config_infrastructure
  failed_when: false

- name: Load user vars from file option (legacy back-compat — highest precedence)
  ansible.builtin.include_vars:
    file: "{{ user_vars_file }}"
  when: user_vars_file is defined and user_vars_file | length > 0

- name: Merge config layers into homelabinfra_config
  ansible.builtin.set_fact:
    homelabinfra_config: "{{
      homelabinfra_defaults | default({})
      | combine(_config_proxmox | default({}), recursive=True)
      | combine({'infrastructure': _config_infrastructure | default({})}, recursive=True)
      | combine(homelabinfra_config | default({}), recursive=True) }}"
```

**What changed vs. today (line-level):**

1. **Removed** the opening `assert` task (`user_vars_file is defined or homelabinfra_config is
   defined`) — see Decision 2.
2. **Kept** the defaults `include_vars` (`file: ../../vars/homelabinfra-defaults.yml`) unchanged — it
   still injects the `homelabinfra_defaults` var (file's top-level wrapper key).
3. **Added** two optional config `include_vars` — `config/proxmox.yml` into named var
   `_config_proxmox`, `config/infrastructure.yml` into named var `_config_infrastructure`, each with
   `failed_when: false`. Both use the task-file-relative path `../../../config/<file>.yml` (Decision 1).
4. **Kept** the legacy `user_vars_file` `include_vars` (unchanged `when:` guard).
5. **Extended** the final `set_fact` merge from two layers to four, all `combine(recursive=True)`:
   `defaults -> _config_proxmox (top-level) -> {infrastructure: _config_infrastructure} ->
   homelabinfra_config (legacy, last-wins)`.

**Why the named-var + wrap shapes are correct (Contract §2):**
- `config/proxmox.yml` has no wrapper; its top-level keys are `proxmox:`/`networks:`/`ansible:`.
  Loading it into `_config_proxmox` and combining that dict at the **top level** of
  `homelabinfra_config` yields `homelabinfra_config.proxmox.*`, `.networks.*`, `.ansible.*` — exactly
  Contract §2 row 2. Keys merge **as-is** (no `host`->`api_host` rename; that is slice 004).
- `config/infrastructure.yml` has no wrapper; loading it into `_config_infrastructure` and combining
  `{'infrastructure': _config_infrastructure}` yields `homelabinfra_config.infrastructure.domain`,
  `.reverse_proxy.*`, … — Contract §2 row 3, matching what `bootstrap.yml` asserts
  (`homelabinfra_config.infrastructure.domain is defined`).

**Files touched:** `ansible/tasks/load-user-vars.yml` (only).

## Decisions

- **Q: Config-file include path across two caller depths (hazard 1).**
  **Decision:** use a **task-file-relative** path — `file: ../../../config/proxmox.yml` and
  `file: ../../../config/infrastructure.yml` (three `../`), **not** a `{{ playbook_dir }}`-based
  expression.
  **Why it works from both depths:** `include_vars` resolves a relative `file:` through Ansible's
  `_find_needle('vars', …)` search-path stack, and that stack always contains **the directory of the
  task file itself** (`ansible/tasks/`), independent of which playbook `import_tasks`-ed it. For each
  search path `p`, path resolution tries `p/vars/<file>` first, so the invariant candidate is
  `ansible/tasks/vars/` + `../../../config/proxmox.yml` = `vars`->`tasks`->`ansible`->repo-root +
  `config/proxmox.yml` = `<repo-root>/config/proxmox.yml`, which is correct for **every** caller. This
  is the *same mechanism* by which the existing `file: ../../vars/homelabinfra-defaults.yml` line in
  this task file resolves to `ansible/vars/homelabinfra-defaults.yml` from all callers today (base
  `ansible/tasks/vars/` + `../../vars/…`); config sits one directory higher (repo-root `config/`
  vs. `ansible/vars/`), so it needs **one more** `../` — three instead of two. Cross-check against the
  two real callers: `playbooks/bootstrap.yml` (playbook_dir = `ansible/playbooks`) and
  `playbooks/apps/_template.yml` (playbook_dir = `ansible/playbooks/apps`). Their `playbook_dir`
  candidates differ by depth (`ansible/playbooks/vars/../../../config/…` -> `<root>/config/…` exists;
  `ansible/playbooks/apps/vars/../../../config/…` -> `ansible/config/…` does not), which is exactly why
  a fixed-`../`-count `playbook_dir` expression cannot serve both — but the **task-file-dir candidate**
  in the stack resolves to `<root>/config/…` for both, so first-found lands on the correct file
  regardless. This is deliberately **not** the meta-README's `{{ playbook_dir }}/../../config/…`
  snippet (which only fits bootstrap's depth) and **not** `_template.yml:42`'s
  `{{ playbook_dir }}/../../../config/…` (correct only because that file lives at the
  `playbooks/apps/` depth); a `playbook_dir` form here would break one of the two depths. Settled by
  inspection — no `NEEDS HUMAN`.

- **Q: Pre-flight assert (hazard 2) — relax or remove?**
  **Decision:** **remove** the assert task entirely.
  **Why:** the assert (`user_vars_file is defined or homelabinfra_config is defined`) rejects the very
  config-file-only workflow this slice enables (that path supplies neither var). Relaxing it to a
  three-way OR is pointless: defaults are always loaded and the merge always produces a non-empty
  `homelabinfra_config`, so any config-source precondition the loader could express is trivially true —
  there is no input state the assert should legitimately reject. Real validation is caller-side and
  already exists: `bootstrap.yml` asserts `homelabinfra_config.proxmox.host` /
  `.proxmox.api_token_id` / `.infrastructure.domain`; the app template asserts `instance`. With the
  loader permissive and config files optional, missing required keys surface as those friendly
  caller-side asserts (e.g. bootstrap's "config/proxmox.yml and config/infrastructure.yml must be
  filled in…"), not as an `include_vars` crash. Removing the always-true guard is cleaner than keeping
  dead validation.

- **Q: Where do the two config files land in the merge, and does user data still win?**
  **Decision:** `_config_proxmox` combined at top level, `{'infrastructure': _config_infrastructure}`
  combined next, `homelabinfra_config` (legacy `user_vars_file` / pre-set extra-var) combined **last**
  — so the legacy path keeps highest precedence per Contract §4 step 4 and the dossier constraint.
  All four layers use `combine(recursive=True)`; no bare `set_fact` replaces a namespace dict
  (`.claude/specs/namespace-merge-discipline.md`).

- **Q: Behaviour when a config file is absent.**
  **Decision:** `failed_when: false` on both config includes; the named var stays undefined and
  `| default({})` makes it a no-op in the merge. Established repo pattern (`_template.yml:44` loads the
  optional instance config the same way). A missing `config/infrastructure.yml` still yields
  `homelabinfra_config.infrastructure == {}`, so a caller's `.infrastructure.domain is defined` is a
  clean `false` (friendly assert), never an undefined-attribute error.

## Verification

The repo's only gates are `lint` and `test` in `.claude/build.yml`; both are static (ansible-lint and
`ansible-playbook --syntax-check`) and neither executes runtime `include_vars` resolution or the
`set_fact` merge. State coverage honestly:

**Gate-provable (must pass, korr-qa runs both):**
- **`test`** (`.claude/gate/test.sh` — `--syntax-check` over every playbook under
  `ansible/playbooks/`): `load-user-vars.yml` is `import_tasks`-ed (static import) into `bootstrap.yml`
  and every subdir playbook, so the syntax check parses the edited task file through those callers.
  A malformed task list, bad `combine` expression, or broken YAML fails the gate. Must stay green.
- **`lint`** (`.claude/gate/lint.sh` — `ansible-lint … tasks …`): lints the edited file directly. The
  relative-path `include_vars` style and multiline `combine` are already used in this same file and
  pass lint today, so the additions must remain lint-clean. Must stay green.

**Inspection-provable (korr-qa's senior read confirms against Contract §2/§4 — not executable by the
gates, so verify by tracing the merge expression):**
- **Top-level proxmox injection:** with a `config/proxmox.yml` carrying `proxmox:`/`networks:`/
  `ansible:`, the merge's second layer (`combine(_config_proxmox …)`) places those under
  `homelabinfra_config` — trace yields `homelabinfra_config.proxmox.host` (as-is; canonical
  `api_host` rename is slice 004), `homelabinfra_config.networks.<name>.cidr`,
  `homelabinfra_config.ansible.ssh_user`.
- **Infrastructure wrap:** the third layer (`combine({'infrastructure': _config_infrastructure} …)`)
  yields `homelabinfra_config.infrastructure.domain`, `.reverse_proxy.*`, etc. — matching
  `bootstrap.yml`'s assert.
- **Missing-file no-op:** absent `config/*.yml` -> `failed_when: false` -> named var undefined ->
  `| default({})` -> that layer contributes nothing; `homelabinfra_config` is defaults (+ any legacy
  input) with `.infrastructure == {}`. No crash.
- **Legacy path unbroken & highest-precedence:** with `user_vars_file` set (e.g. an existing test
  using `vars/user-vars-example.yml`), its self-wrapping `homelabinfra_config` is the **last**
  `combine`, so it overrides colliding keys from defaults/proxmox/infrastructure — Contract §4 step 4.
- **Merge discipline:** every layer is `combine(recursive=True)`; no bare `set_fact` replaces a
  namespace dict (`.claude/specs/namespace-merge-discipline.md`).

## Run log

### round 1

**[implementer]**

Replaced the entire body of `ansible/tasks/load-user-vars.yml` with the exact four-layer loader
given verbatim in the plan's `## Plan` section — no deviation. Diff summary:

- Removed the opening `assert` task (`user_vars_file is defined or homelabinfra_config is defined`)
  per Decision 2.
- Kept the defaults `include_vars` (`file: ../../vars/homelabinfra-defaults.yml`) unchanged.
- Added two optional `include_vars` tasks, each `failed_when: false`: `config/proxmox.yml` ->
  named var `_config_proxmox` (path `../../../config/proxmox.yml`), `config/infrastructure.yml` ->
  named var `_config_infrastructure` (path `../../../config/infrastructure.yml`).
- Kept the legacy `user_vars_file` `include_vars` unchanged (renamed task label only, per plan text).
- Extended the final `set_fact` to a four-layer `combine(recursive=True)` chain: defaults ->
  `_config_proxmox` (top-level) -> `{infrastructure: _config_infrastructure}` -> legacy
  `homelabinfra_config` (last-wins).

**Files touched:** `ansible/tasks/load-user-vars.yml` (only — confirmed via `git status`/`git diff`;
no other tracked file was modified by this round).

**Path-resolution check (hazard 1, plan's Decision 1):** ran the `test` gate (`--syntax-check` over
every playbook under `ansible/playbooks/`) and confirmed both caller depths that `import_tasks` this
file parse cleanly through it: `playbooks/bootstrap.yml` (playbook_dir = `ansible/playbooks`) and
`playbooks/apps/_template.yml` / `playbooks/apps/remove.yml` (playbook_dir =
`ansible/playbooks/apps`) all printed `playbook: <path>` (syntax-check success) with the new
`../../../config/*.yml` includes in place — consistent with the task-file-relative resolution the
plan's Decision 1 argues for.

**Gate evidence:**

Note on the two `lint`/`test` commands literally given in the plan/build.yml
(`cd /mnt/c/Users/korr/source/repos/homelab-infra`): that path does not exist in this checkout
(`No such file or directory`, exit 1) — this machine's WSL mount is
`/mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra`. Ran both gates at the corrected path instead, per
the task's fallback instruction.

- `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh'`
  → exit 0. Tail:
  ```
  Passed: 0 failure(s), 0 warning(s) in 55 files processed of 64 encountered. Profile 'min' was
  required, but 'production' profile passed.
  ```
- `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh'`
  → exit 0 (confirmed by piping to a log file and echoing `$?` directly, to rule out a WSL-relay
  exit-code quirk seen on the very first invocation — see note below). Output includes `[ERROR]`
  lines for `playbooks/docker/create-docker-host.yml` (missing `docker` role dir),
  `playbooks/maintenance/restart-app.yml` and `tail-applog.yml` (`instance` undefined — expected,
  those playbooks require a runtime `-e instance=…`), and `playbooks/stacks/rollback-container.yml`
  (`Empty playbook, nothing to do`). **Verified pre-existing and unrelated to this change**: stashed
  the edit, re-ran `test.sh` against unmodified `master`'s `load-user-vars.yml` — identical `[ERROR]`
  lines appear and the gate still exits 0 (Ansible's `--syntax-check` treats these as non-fatal
  diagnostics, not exit-code failures); popped the stash to restore the edit. `bootstrap.yml`,
  `apps/_template.yml`, and `apps/remove.yml` — the three playbooks that actually
  `import_tasks: ../../tasks/load-user-vars.yml` at both relevant depths — all report
  `playbook: <path>` (clean parse).
  - WSL exit-code note: the very first raw invocation of this gate command (before the explicit
    `; echo $?` capture) was reported by the Bash tool as exit 1 with this same output, for both the
    modified file and unmodified `master`. Re-running with an explicit `echo RC=$?` inside the same
    `wsl bash -lc '...'` call consistently returns 0 on both the modified branch and unmodified
    `master`, so the "exit 1" was a Bash-tool/`wsl` exit-status relay artifact, not the script's real
    result — reporting this honestly per the task's instructions rather than picking whichever run
    looked better.

**Decisions:** none needed — the plan's `## Decisions` section already resolves both hazards
(config-file relative path depth, assert removal) and this round implemented them verbatim.

**Notes for reviewer:** `.claude/settings.local.json` (modified) and `.claude/agents/fable-groomer.md`
(untracked) were already present in the working tree before this round started (visible in the
session's initial `git status` snapshot) — not touched by this implementation and not part of this
plan's scope.

### [reviewer] 2026-07-05

[reviewer] verdict: PASS

- Diff (`git diff -- ansible/tasks/load-user-vars.yml`) matches the plan's `## Plan` verbatim block
  exactly, line for line: assert task removed, defaults include unchanged, two new
  `config/proxmox.yml` / `config/infrastructure.yml` includes (named vars `_config_proxmox` /
  `_config_infrastructure`, both `failed_when: false`, both `../../../config/...` three-dot paths),
  legacy `user_vars_file` include unchanged, and the four-layer `combine(recursive=True)` merge
  chain (defaults → proxmox top-level → `{infrastructure: ...}` → legacy `homelabinfra_config`
  last-wins).
- Cross-checked the merge shapes against `ansible/vars/CONTRACT.md` §2/§4/§5 directly: load-map row
  order, wrapper placement (`proxmox`/`networks`/`ansible` at top level, whole file under
  `.infrastructure`), and precedence order all match the contract verbatim. No key renaming
  attempted (correctly out of scope, slice 004).
- Verified Decision 1's path-resolution claim against the actual installed Ansible core (2.20.1)
  source rather than taking it on inspection alone: `include_vars`'s action plugin calls
  `_find_needle('vars', file)` → `Task.get_search_path()` (`ansible/playbook/base.py`), which builds
  `task_dir = os.path.dirname(self.get_path())` from the task's own YAML origin file
  (`ansible/tasks/load-user-vars.yml`), not the invoking playbook's `playbook_dir`. Traced
  `path_dwim_relative_stack` (`ansible/parsing/dataloader.py`): the first, highest-priority search
  candidate is `<task_dir>/vars/<relative-file>`, i.e.
  `ansible/tasks/vars/../../../config/proxmox.yml` → `<repo-root>/config/proxmox.yml` — correct
  regardless of which playbook (`bootstrap.yml` at `playbooks/` depth or `apps/_template.yml` at
  `playbooks/apps/` depth) imports the task. Confirms the plan's claim: three dots is depth-agnostic
  by construction, not coincidence.
- Confirmed missing-file handling doesn't crash: `include_vars`'s action plugin catches
  `AnsibleFileNotFound` internally and returns a normal failed result (not a raised fatal), so
  `failed_when: false` on both config includes correctly suppresses it on a clean checkout.
- Namespace-merge-discipline (`.claude/specs/namespace-merge-discipline.md`) satisfied: every layer
  of the final `set_fact` is `combine(recursive=True)`, no bare dict replacement, no `default(omit)`
  stored in a fact. Framework reflexes (`.claude/specs/framework.md`) checked — no bare `set_fact` on
  a namespace dict, no fact-sourced arithmetic, no secrets in module args, no
  `hosts: proxmox_nodes`/`run_once` pattern in this file. No secrets, injection, or swallowed-error
  reflex issues — the `failed_when: false` permissiveness is the plan's explicit, documented design
  (caller-side asserts are the real gate), not a silently dropped failure.
- Gate evidence: re-ran both gates at the substituted path.
  - `lint.sh` → exit 0, `Passed: 0 failure(s), 0 warning(s) in 55 files processed of 64 encountered` —
    matches implementer's report.
  - `test.sh` → implementer reported an initial raw "exit 1" observation later reconciled to exit 0
    via explicit `$?` capture, flagged as a possible Bash-tool/WSL exit-status relay artifact. Given
    that inconsistency, re-ran independently: a plain re-run through this session's Bash tool also
    reported "Exit code 1" in the tool-result metadata, but `wsl bash -lc '... ; echo "RC=$?"'` (and
    a per-playbook direct run of the four flagged playbooks) consistently returned `RC=0`. Read
    `.claude/gate/test.sh`: it sets `rc=1` only when an individual `ansible-playbook --syntax-check`
    invocation itself returns non-zero, and empirically each of the four flagged playbooks
    (`docker/create-docker-host.yml` missing role, `restart-app.yml`/`tail-applog.yml` undefined
    `instance`, `stacks/rollback-container.yml` empty playbook) exits 0 from `--syntax-check` despite
    printing `[ERROR]` — confirming the script's real, final exit code is 0 and the "Exit code 1"
    surfaced by the tool wrapper is a reporting artifact independent of the script/gate itself, not a
    gate failure. This corroborates rather than contradicts the implementer's account. Gate evidence
    stands as green.
- No trivia fixed — nothing needed it.

[qa] verdict: PASS
- Senior pass: diff re-read against Contract §2/§4 and the plan's verbatim block — exact match; merge
  discipline (`combine(recursive=True)` on all four layers, legacy last-wins) confirmed.
- Spot-ran both gates myself with explicit RC capture (`; echo "GATE_RC=$?"`): lint → GATE_RC=0
  (`Passed: 0 failure(s), 0 warning(s)`); test → GATE_RC=0. The Bash-tool "exit 1" both agents saw is
  confirmed a WSL relay artifact; the scripts' real exit codes are 0. Pre-existing `[ERROR]`
  diagnostics (docker role, undefined `instance`, empty rollback playbook) verified unrelated —
  identical on unmodified master.
- Lifted upward (not this plan's scope): `.claude/build.yml` gate commands carry a stale WSL path
  (`/mnt/c/Users/korr/source/repos/...`); this machine's checkout is
  `/mnt/c/Users/kevin/GitHub/hardKOrr/...`. Both agents and QA substituted the real path. Fix at the
  next korr-design pass.
