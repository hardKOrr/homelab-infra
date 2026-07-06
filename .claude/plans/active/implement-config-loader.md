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
