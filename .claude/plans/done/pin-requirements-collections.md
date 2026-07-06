# pin-requirements-collections

**Type:** fix

**Depends on:** establish-ansible-gate (done — created the gate venv whose verified collection
versions this plan promotes into `requirements.yml`)

**Spec:** .claude/specs/framework.md ("Language & toolchain"); meta slice 007
(`.claude/meta/007-requirements-collections/README.md`)

## Goal

Make `ansible/requirements.yml` the single, deterministic source of truth for the collections
this codebase actually uses: add the two missing collections (`community.docker`,
`community.general`), pin all four to the exact versions already verified in the gate venv, and
fold in the two doc lines that were explicitly waiting for this slice (`.claude/build.yml`
bootstrap comment, `.claude/specs/framework.md` toolchain bullet).

## Context

**Current state.** `ansible/requirements.yml` is 4 lines:

```yaml
---
collections:
  - name: community.proxmox
  - name: ansible.utils
```

Unpinned, and missing two collections the code already imports. A fresh clone +
`ansible-galaxy collection install -r ansible/requirements.yml` cannot run `guest-bootstrap.yml`
or any docker app.

**Where each collection is used** (verified by grep 2026-07-06):

- `community.proxmox` — `inventory/proxmox.yml`, `tasks/proxmox/lxc-create.yml:74,82,84,122`,
  `tasks/proxmox/vm-create.yml:78,86,142`, plus `tasks/proxmox/todo/` staging stubs.
- `ansible.utils` — `tasks/network/generate-ip.yml:49,56,60`, `tasks/proxmox/lxc-create.yml:37`,
  `tasks/proxmox/vm-create.yml:39` (all `ansible.utils.ipaddr`).
- `community.general` — `tasks/guest-bootstrap.yml:48` (`community.general.timezone`);
  `.claude/CLAUDE.md` mandates the `community.general.bitwarden` lookup for all Vaultwarden-held
  secrets (future wiring code will use it).
- `community.docker` — `roles/_template-docker/tasks/main.yml:31,36`
  (`docker_compose_v2_pull`, `docker_compose_v2`), `roles/_template-docker/handlers/main.yml:3`,
  plus `tasks/docker/todo/` staging stubs.

No other external collection is referenced anywhere under `ansible/` (`ansible.builtin` is core,
not a Galaxy collection). So the complete set is exactly these four.

**The known-good versions.** `.claude/plans/done/establish-ansible-gate.md` installed and
verified these in the gate venv (`~/.venvs/homelab-ansible` in WSL), recorded in the
`.claude/build.yml` bootstrap comment:

- `community.proxmox` **2.0.0**
- `ansible.utils` **6.0.3**
- `community.general` **13.1.0**
- `community.docker` **5.2.1**

The whole gate (lint + syntax-check over 64 files / 11 playbooks) runs green against this exact
combination on ansible-core 2.18.1, so these are the pins. **Pin style is exact (`==`)**, not
major-version ranges: the repo's established convention is exact pins with a verified-date
comment (`.claude/gate/requirements-dev.txt` pins `ansible-core==2.18.1` etc. with the rationale
"reproducible on a fresh WSL distro"), and the platform philosophy is deterministic known-good
("fire-and-forget: create correct once"). Users get version bumps via git pull when the repo
re-verifies; they never resolve versions themselves. `ansible-galaxy` pin syntax in a
requirements file is `version: "==2.0.0"` or bare `version: "2.0.0"` (exact match) — use the
plain exact form `version: "2.0.0"`.

**Two doc lines are contractually waiting on this slice** and must change in the same diff:

1. `.claude/build.yml:11-13` — the bootstrap comment installs the four collections by explicit
   `name:==version` args and carries the note *"(when meta 007 lands, switch the galaxy line to:
   `-r ansible/requirements.yml`)"*. Replace the explicit `ansible-galaxy collection install
   community.proxmox:==2.0.0 ...` line with `ansible-galaxy collection install -r
   ansible/requirements.yml` and drop the now-satisfied parenthetical. Only the comment block
   changes; the `lint:`/`test:` commands and everything else in `build.yml` stay byte-identical.
2. `.claude/specs/framework.md:8-10` — the toolchain bullet ends *"`ansible/requirements.yml`
   lists names unpinned; meta slice 007 owns reconciling the two."* Rewrite that bullet to state
   the reconciled truth: the four collections are pinned exactly in `ansible/requirements.yml`
   (the single source of truth), and the gate venv installs from it. Keep the bullet's shape and
   the four name+version citations.

Also update the meta slice status: `.claude/meta/007-requirements-collections/README.md` line 3
`**Status:** open` → `**Status:** done (plan pin-requirements-collections)` and the matching row
in `.claude/meta/INDEX.md` (`| 007 | ... | open |` → `done`). This is the repo's existing
pattern for landed slices.

**Scope amendment (user directive, 2026-07-06, at /korr-qa invocation):** 007 is the last open
00# slice, so this change also closes the whole 0XX tier. Slices 000-006 all landed via done
plans but their READMEs and INDEX rows were never flipped (verified 2026-07-06: every
`.claude/meta/*/README.md` still says `**Status:** open`). Flip them in the same diff, each
README citing the plan(s) that landed it:

| Slice | README status becomes |
|---|---|
| 000 | `**Status:** done (plan variable-loading-contract)` |
| 001 | `**Status:** done (plan implement-config-loader)` |
| 002 | `**Status:** done (plan reconcile-config-example)` |
| 003 | `**Status:** done (plan filter-proxmox-module-params)` |
| 004 | `**Status:** done (plan unify-proxmox-key-naming)` |
| 005 | `**Status:** done (plan settle-instance-config-schema)` |
| 006 | `**Status:** done (plans fix-generate-ip-allocation-loop, fix-generate-ip-instance-clobber)` |

and the eight 0XX rows in `.claude/meta/INDEX.md` (000-007) get their status cell `open` →
`done`. Only line 3 of each README and only the status cells in INDEX.md change.

**Environment facts for verification** (from prior runs, recorded 2026-07-04/06):

- Gates run from the repo root: `wsl bash -lc 'bash .claude/gate/lint.sh'` and
  `wsl bash -lc 'bash .claude/gate/test.sh'`. Read exit codes from the Bash tool's reported
  status, never `$?` through the relay.
- The `test` gate currently exits 1 with exactly three pre-existing failures
  (`playbooks/maintenance/restart-app.yml`, `playbooks/maintenance/tail-applog.yml` — `'instance'
  is undefined`; `playbooks/stacks/rollback-container.yml` — empty playbook), recorded in
  `.claude/plans/done/fix-generate-ip-allocation-loop.md`. Accept exit 1 only if those three are
  the only failures.
- When passing absolute `/mnt/c/...` POSIX paths as arguments through the Windows-side Git Bash
  relay, prepend `MSYS_NO_PATHCONV=1` (observed mangling during the 006 run).
- `ansible-galaxy` lives in the venv: `~/.venvs/homelab-ansible/bin/ansible-galaxy`. A
  fresh-install proof needs network access to galaxy.ansible.com; install to a throwaway `-p`
  path so the venv's real collection tree is untouched. `ansible-galaxy` resolves declared
  dependencies automatically (e.g. `community.docker` may pull a small helper collection);
  "installs all four" tolerates extra dependency collections appearing.

## Acceptance criteria

- `ansible/requirements.yml` lists exactly four collections — `community.proxmox`,
  `ansible.utils`, `community.docker`, `community.general` — each with an exact `version:` pin
  matching the gate-verified set (2.0.0, 6.0.3, 5.2.1, 13.1.0 respectively).
- Fresh-install proof: `ansible-galaxy collection install -r ansible/requirements.yml -p <empty
  throwaway dir>` run in the WSL gate venv exits 0 and the throwaway path contains all four
  collections at the pinned versions (dependency collections pulled by galaxy are allowed).
- Grep proof: every `community.*` and `ansible.utils` FQCN under `ansible/` belongs to a
  collection named in `requirements.yml` (meta 007 acceptance, checkable from the grep output).
- `.claude/build.yml` bootstrap comment installs via `-r ansible/requirements.yml`; the "when
  meta 007 lands" note is gone; `lint:`/`test:` commands unchanged.
- `.claude/specs/framework.md` toolchain bullet states requirements.yml is the pinned source of
  truth; no "meta slice 007 owns reconciling" sentence remains.
- Meta slices 000-007 all marked done: each 0XX README's `**Status:**` line cites its landing
  plan(s) per the Context table, and all eight 0XX rows in `.claude/meta/INDEX.md` read `done`.
  No 1XX+ row or README changes.
- The `lint` and `test` gates from `.claude/build.yml` pass with no new failures (test exits 1
  with only the three recorded pre-existing failures).

## Plan

Five files change, all part of the one logical change (pin the collection set, then fold in the
two doc lines and the meta-status flips that were contractually waiting on this slice). This is a
single reviewable change, not two — the doc/meta edits are the bookkeeping half of promoting
`requirements.yml` to the source of truth and cannot land separately without leaving the repo
half-reconciled. No `ansible/` code file changes; only the requirements manifest, two doc lines,
and two meta-status cells. Work is "test-first" in the sense that the gates plus the fresh-install
value proof in Verification are the proof — there is no unit harness.

### Step 1 — Rewrite `ansible/requirements.yml` (functional change)

**Replace the entire file** (current 4 lines) with this **verbatim** content:

```yaml
---
# Galaxy collections this codebase imports. Pinned to the exact versions verified in the gate
# venv (~/.venvs/homelab-ansible) — see .claude/build.yml. This file is the single source of
# truth: the gate bootstrap and a fresh clone both install from it via
# `ansible-galaxy collection install -r ansible/requirements.yml`. Users get version bumps via
# git pull when the repo re-verifies; they never resolve versions themselves.
# Verified 2026-07-06: gate (lint + syntax-check) green on ansible-core 2.18.1 with these pins.
collections:
  - name: community.proxmox
    version: "2.0.0"
  - name: ansible.utils
    version: "6.0.3"
  - name: community.docker
    version: "5.2.1"
  - name: community.general
    version: "13.1.0"
```

Notes for the implementer (do exactly this, no more):

- **Four collections, in this order** — the two pre-existing (`community.proxmox`, `ansible.utils`)
  kept in place, then `community.docker`, then `community.general` appended (Decision D2).
- **Pin form is the bare exact string** `version: "2.0.0"` — not `"==2.0.0"`, not a range. A bare
  version in an `ansible-galaxy` requirements file is an exact match (Decision D1).
- **Quote every version** to keep it a string (`"13.1.0"` not `13.1.0`) — bare numerics are fine
  here but quoting is consistent with the exact-pin intent and avoids any YAML numeric coercion of
  a value like a future `1.30`.
- **Keep the header comment** — it mirrors the verified-date convention of
  `.claude/gate/requirements-dev.txt` (Decision D3).

### Step 2 — Rewrite the `build.yml` bootstrap comment (doc line 1)

In `.claude/build.yml`, **replace exactly these current lines 11-13**:

```
#   ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install \
#       community.proxmox:==2.0.0 ansible.utils:==6.0.3 community.general:==13.1.0 community.docker:==5.2.1
#   (when meta 007 lands, switch the galaxy line to: -r ansible/requirements.yml)
```

with this **verbatim** single line:

```
#   ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```

Only this comment block changes. The `lint:` / `test:` command lines (28-29) and every other line
in `build.yml` stay byte-identical (Decision D4).

### Step 3 — Rewrite the `framework.md` toolchain bullet (doc line 2)

In `.claude/specs/framework.md`, **replace exactly this current bullet (lines 8-10)**:

```
- Ansible (YAML) throughout. Collections pinned at gate-bootstrap time: `community.proxmox`
  2.0.0, `ansible.utils` 6.0.3, `community.general` 13.1.0, `community.docker` 5.2.1.
  `ansible/requirements.yml` lists names unpinned; meta slice 007 owns reconciling the two.
```

with this **verbatim** bullet:

```
- Ansible (YAML) throughout. The four Galaxy collections this repo uses are pinned exactly in
  `ansible/requirements.yml` — the single source of truth: `community.proxmox` 2.0.0,
  `ansible.utils` 6.0.3, `community.docker` 5.2.1, `community.general` 13.1.0. The gate venv
  installs from it (`ansible-galaxy collection install -r ansible/requirements.yml`).
```

Keeps the bullet's shape and all four name+version citations; no "meta slice 007 owns reconciling"
sentence remains (Decision D5).

### Step 4 — Flip the status line in all eight 0XX slice READMEs

In each of the eight `.claude/meta/00X-*/README.md` files, **replace exactly line 3**
(`**Status:** open`) with the slice's row from the Context table (Decisions D6, D8):

- `000-variable-loading-contract` → `**Status:** done (plan variable-loading-contract)`
- `001-config-loader` → `**Status:** done (plan implement-config-loader)`
- `002-reconcile-config-example` → `**Status:** done (plan reconcile-config-example)`
- `003-filter-proxmox-module-params` → `**Status:** done (plan filter-proxmox-module-params)`
- `004-proxmox-key-naming` → `**Status:** done (plan unify-proxmox-key-naming)`
- `005-instance-config-schema` → `**Status:** done (plan settle-instance-config-schema)`
- `006-generate-ip-combine` → `**Status:** done (plans fix-generate-ip-allocation-loop, fix-generate-ip-instance-clobber)`
- `007-requirements-collections` → `**Status:** done (plan pin-requirements-collections)`

No other line in any README changes.

### Step 5 — Flip all eight 0XX rows in `INDEX.md`

In `.claude/meta/INDEX.md`, in the `## 0XX — Foundation` table (rows at lines 9-16), change
**only the status cell** `open` → `done` on each of the eight rows (000-007). Every other cell
(slice link, Depends on, Blocks) and every 1XX+ table stays byte-identical. E.g. row 16 becomes:

```
| 007 | [requirements.yml collections](007-requirements-collections/README.md) | done | none | any docker app, guest-bootstrap |
```

## Decisions

- **D1 — Exact pin, bare string form `version: "2.0.0"`.** The dossier fixes exact-pin style
  (repo convention: `.claude/gate/requirements-dev.txt` pins `ansible-core==2.18.1` exactly with a
  verified-date rationale; platform philosophy is deterministic known-good). In an `ansible-galaxy`
  requirements file a bare `version:` value is an exact match, so `"2.0.0"` and `"==2.0.0"` install
  the same collection; the plain form is chosen for readability. No ranges — users get bumps via
  git pull, never resolve versions themselves. Settled by the Context.
- **D2 — Order: keep the two existing entries first, then append `community.docker`,
  `community.general`.** Minimises the diff on the two pre-existing lines (they gain only a
  `version:` child) and matches the acceptance-criteria enumeration order
  (proxmox, utils, docker, general → 2.0.0, 6.0.3, 5.2.1, 13.1.0). Groomer's call; order is
  functionally irrelevant to `ansible-galaxy`.
- **D3 — Add a header comment with the verified-date, mirroring `requirements-dev.txt`.** The repo's
  established exact-pin convention pairs the pin with a rationale + `Verified <date>` comment
  (`requirements-dev.txt` lines 1-4). Applying the same shape here makes the "single source of
  truth" claim self-documenting and states which gate run verified the set (2026-07-06,
  ansible-core 2.18.1). Groomer's call.
- **D4 — `build.yml`: collapse the three-line explicit-install comment to the single
  `-r ansible/requirements.yml` line and drop the parenthetical.** The parenthetical
  ("when meta 007 lands, switch the galaxy line to…") is now satisfied, so it is deleted, and the
  explicit `name:==version` args are replaced by the `-r` form they pointed to. Only the comment
  changes; `lint:`/`test:` and all machinery stay byte-identical. Mandated by the Context.
- **D5 — `framework.md`: rewrite the bullet to state the reconciled truth, keeping all four
  name+version citations.** The old bullet's "lists names unpinned; meta slice 007 owns reconciling"
  is now false; the new bullet names `requirements.yml` as the pinned single source of truth and
  cites the four pins. Shape (one bullet, four citations, install-from-it note) preserved. Mandated
  by the Context.
- **D6 — Meta 007 marked `done (plan pin-requirements-collections)` in the README and `done` in the
  INDEX row.** This is the repo's landed-slice pattern (a plan reference in the README status, a
  bare `done` in the index cell). The `Depends on` / `Blocks` cells are left unchanged. Settled by
  the Context.
- **D7 — This stays one plan, not a split.** The functional pin and the doc/meta reconciliation are
  the two halves of one promotion of `requirements.yml` to source-of-truth; splitting would leave
  `framework.md` claiming "unpinned" against a pinned file for a review cycle. One reviewable
  change. Groomer's call.
- **D8 — Close the whole 0XX tier in this diff (user directive at /korr-qa invocation,
  2026-07-06).** 007 is the last open 00# slice; 000-006 all landed via done plans
  (mapping verified against `.claude/plans/done/` — see the Context table) but their
  README/INDEX statuses were never flipped. Status-only edits: line 3 of each 0XX README, the
  eight status cells in INDEX.md's 0XX table. No 1XX+ status is touched — those slices have not
  landed.

## Verification

**Gates** (the only two defined in `.claude/build.yml`, run from the repo root; read exit codes
from the Bash tool's reported status, not `$?`, per the shell-relay note in `build.yml`):

- **lint** — `wsl bash -lc 'bash .claude/gate/lint.sh'`
  Must exit **0** with no new findings. The change is valid YAML in `requirements.yml` and comment/
  doc/status text elsewhere; nothing here can introduce an ansible-lint failure. Output stays
  `Passed: 0 failure(s), 0 warning(s) on 64 files.`
- **test** — `wsl bash -lc 'bash .claude/gate/test.sh'`
  Syntax-checks every playbook; none of the five changed files is a playbook, so behaviour is
  unchanged. This gate exits **1** because of three pre-existing, unrelated failures —
  `playbooks/maintenance/restart-app.yml` and `tail-applog.yml` (`'instance' is undefined`) and
  `playbooks/stacks/rollback-container.yml` (empty playbook), recorded in
  `.claude/plans/done/fix-generate-ip-allocation-loop.md`. Accept exit 1 only if those three are the
  *only* failures.

**Value proof (mandatory — the gates never run `ansible-galaxy install`, so only this proves a
fresh clone resolves all four pins).** Install from the manifest into a throwaway `-p` path inside
the WSL gate venv, then list what landed. Needs network access to galaxy.ansible.com. Run from the
repo root:

```
MSYS_NO_PATHCONV=1 wsl bash -lc 'rm -rf /tmp/reqcheck && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml -p /tmp/reqcheck && echo "---INSTALLED---" && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection list -p /tmp/reqcheck'
```

- **`MSYS_NO_PATHCONV=1`** is prepended because the leading-slash argument `/tmp/reqcheck` is
  passed through the Windows-side Git Bash relay, which otherwise rewrites it to a Windows path
  (documented in `build.yml`, observed on the 006-run value proof).
- The install writes only into `/tmp/reqcheck`; the venv's real collection tree
  (`~/.ansible/collections` / venv default) is untouched.

Expected result: the install **exits 0** (read from the Bash tool's reported status), and the
`collection list` table under `# /tmp/reqcheck/ansible_collections` shows all four at the pinned
versions:

```
Collection         Version
------------------ -------
ansible.utils      6.0.3
community.docker   5.2.1
community.general  13.1.0
community.proxmox  2.0.0
```

Dependency collections pulled automatically by galaxy (e.g. `community.docker` declares a
dependency on `community.library_inventory_filtering_v1`) **may also appear** in the list — that is
allowed; the pass condition is that the four named collections are present at exactly the pinned
versions and the install exited 0. Delete the throwaway path after the run:
`wsl bash -lc 'rm -rf /tmp/reqcheck'`.

**Grep proof (meta 007 acceptance — every external FQCN under `ansible/` belongs to a declared
collection).** Run from the repo root:

```
wsl bash -lc "grep -rhoE '(community\.[a-z_0-9]+|ansible\.utils)\.[a-z_0-9]+' ansible --include='*.yml' | sed -E 's/\.[a-z_0-9]+\$//' | sort -u"
```

Expected output — exactly these four namespace.collection prefixes, every one of them declared in
`requirements.yml`, and nothing else:

```
ansible.utils
community.docker
community.general
community.proxmox
```

(`ansible.builtin` is core, not a Galaxy collection, and is intentionally not matched. Matches
inside `**/todo/` staging stubs resolve to `community.proxmox` / `community.docker`, both declared.)

**korr-qa senior pass confirms, from the diff alone:**

- **Twelve files changed and no more** — `git diff --stat` shows exactly
  `ansible/requirements.yml`, `.claude/build.yml`, `.claude/specs/framework.md`,
  `.claude/meta/INDEX.md`, and the eight `.claude/meta/00X-*/README.md` files. No `ansible/`
  code (task/playbook/role) file is touched.
- **`requirements.yml`** lists exactly four collections in order proxmox / utils / docker / general,
  each with a bare exact `version:` pin (`2.0.0`, `6.0.3`, `5.2.1`, `13.1.0`), plus the verified-date
  header comment. No ranges, no `==` prefix, no fifth collection.
- **`build.yml`** — the bootstrap comment installs via `-r ansible/requirements.yml`; the explicit
  `community.proxmox:==2.0.0 …` args and the "when meta 007 lands" parenthetical are gone; the
  `lint:` / `test:` lines and everything else are byte-identical to master.
- **`framework.md`** — the toolchain bullet names `ansible/requirements.yml` as the pinned single
  source of truth with all four name+version citations; no "lists names unpinned" / "meta slice 007
  owns reconciling" sentence remains.
- **Meta status** — each 0XX README's line 3 reads `**Status:** done (plan …)` per the Context
  table (007 → pin-requirements-collections); all eight 0XX rows in `INDEX.md` read `done`. No
  other cell, no 1XX+ row, and no other README line changed.
- **Gate evidence** — lint exit 0 (64 files clean); test exit 1 with only the three named
  pre-existing failures. Value-proof output shows install exit 0 and all four collections at the
  pinned versions in the throwaway path (dependency collections allowed). Grep-proof output is
  exactly the four declared prefixes.

## Run log

### [implementer] 2026-07-06

**Changes made** — exactly the twelve files the plan names, per the verbatim Steps 1-5:

1. `ansible/requirements.yml` — rewritten in full: header comment plus four collections in order
   `community.proxmox` `2.0.0`, `ansible.utils` `6.0.3`, `community.docker` `5.2.1`,
   `community.general` `13.1.0`, each with a bare quoted exact `version:` pin.
2. `.claude/build.yml` — collapsed the three-line bootstrap comment (explicit
   `name:==version` args + "when meta 007 lands" parenthetical) to the single line
   `#   ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml`.
   `lint:`/`test:` lines and everything else untouched.
3. `.claude/specs/framework.md` — rewrote the toolchain bullet to name
   `ansible/requirements.yml` as the pinned single source of truth with all four
   name+version citations; removed the "lists names unpinned; meta slice 007 owns
   reconciling" sentence.
4. Eight `.claude/meta/00X-*/README.md` files (000-007) — line 3 `**Status:** open` →
   `**Status:** done (plan ...)` per the Context table (000 variable-loading-contract,
   001 implement-config-loader, 002 reconcile-config-example,
   003 filter-proxmox-module-params, 004 unify-proxmox-key-naming,
   005 settle-instance-config-schema, 006 fix-generate-ip-allocation-loop +
   fix-generate-ip-instance-clobber, 007 pin-requirements-collections).
5. `.claude/meta/INDEX.md` — the eight 0XX rows (000-007) in the "0XX — Foundation" table:
   status cell `open` → `done`. No other cell, no 1XX+ row touched.

`git status --short` confirms exactly these twelve files plus the plan file itself (this run
log) changed; no `ansible/` task/playbook/role file was touched.

**Gate evidence:**

- `wsl bash -lc 'bash .claude/gate/lint.sh'` — exit **0**.
  Output: `Passed: 0 failure(s), 0 warning(s) on 64 files. Profile 'min' was required, but
  'production' profile passed.`
- `wsl bash -lc 'bash .claude/gate/test.sh'` — exit **1**, exactly the three recorded
  pre-existing failures:
  - `playbooks/maintenance/restart-app.yml` — `'instance' is undefined` (invalid `hosts` value)
  - `playbooks/maintenance/tail-applog.yml` — `'instance' is undefined` (invalid `hosts` value)
  - `playbooks/stacks/rollback-container.yml` — `Empty playbook, nothing to do`
  No new failures. Matches `.claude/plans/done/fix-generate-ip-allocation-loop.md`.

**Value proof (mandatory, needs network):**

Ran the exact command from Verification first:
```
MSYS_NO_PATHCONV=1 wsl bash -lc 'rm -rf /tmp/reqcheck && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml -p /tmp/reqcheck && echo "---INSTALLED---" && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection list -p /tmp/reqcheck'
```
This exited 0 but printed `Nothing to do. All requested collections are already installed.`
and the `collection list` showed only the pre-existing default path
(`/root/.ansible/collections/ansible_collections`) — `/tmp/reqcheck/ansible_collections` was
created but empty. Root cause: the gate venv already has all four collections installed at the
default collections path from the `establish-ansible-gate` run, so `ansible-galaxy` treats the
`-r` requirement as already satisfied anywhere in the search path and never populates the `-p`
target. This is an environment-state quirk of `ansible-galaxy`'s satisfied-check, not a defect in
`requirements.yml` or the plan's command.

To get an actual fresh-install proof into the throwaway path, re-ran with `--force` added
(same command otherwise):
```
MSYS_NO_PATHCONV=1 wsl bash -lc 'rm -rf /tmp/reqcheck && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml -p /tmp/reqcheck --force && echo "---INSTALLED---" && ~/.venvs/homelab-ansible/bin/ansible-galaxy collection list -p /tmp/reqcheck'
```
Exit **0**. Output under `# /tmp/reqcheck/ansible_collections`:
```
Collection                               Version
---------------------------------------- -------
ansible.utils                            6.0.3
community.docker                         5.2.1
community.general                        13.1.0
community.proxmox                        2.0.0
```
All four at exactly the pinned versions. (The `community.library_inventory_filtering_v1`
dependency was skipped as "already installed" from the default path — it is not one of the four
required collections, so this does not affect the pass condition.) Cleaned up afterwards:
`wsl bash -lc 'rm -rf /tmp/reqcheck'`.

**Grep proof:**
```
wsl bash -lc "grep -rhoE '(community\.[a-z_0-9]+|ansible\.utils)\.[a-z_0-9]+' ansible --include='*.yml' | sed -E 's/\.[a-z_0-9]+\$//' | sort -u"
```
Output — exactly the four expected prefixes, nothing else:
```
ansible.utils
community.docker
community.general
community.proxmox
```

**Note for reviewer:** the plan's exact value-proof command (without `--force`) does not, by
itself, prove anything in this environment because the gate venv already carries the four
collections from `establish-ansible-gate` — `ansible-galaxy` short-circuits to "Nothing to do"
against the default path and leaves the `-p` throwaway path empty. Adding `--force` is the
minimal change needed to force a real install into the throwaway path and is not a change to any
plan file, decision, or acceptance criterion — it only affects how the ad-hoc verification
command was invoked. Flagging this in case a future re-verification of this same proof hits the
same "Nothing to do" result and needs the same `--force` addition.

No decisions needed, no blockers. Working tree left uncommitted on `fix/pin-requirements-collections` for review.

### [reviewer] 2026-07-06

[reviewer] verdict: PASS

- `ansible/requirements.yml` matches the Step 1 verbatim block byte-for-byte (four collections,
  correct order, bare-string exact pins, header comment). Re-ran the grep proof myself: output is
  exactly `ansible.utils` / `community.docker` / `community.general` / `community.proxmox`,
  matching the recorded evidence.
- `.claude/build.yml` — only the bootstrap comment block changed (3 lines → 1, parenthetical
  gone); `lint:`/`test:` lines and everything else byte-identical to master. Verified with
  `git diff master -- .claude/build.yml`.
- `.claude/specs/framework.md` toolchain bullet rewritten as specified; no "lists names
  unpinned"/"meta slice 007 owns reconciling" text remains.
- All eight `.claude/meta/00X-*/README.md` status lines and all eight INDEX.md status cells
  flipped exactly per the Context table; no other line/cell touched in any of the nine files.
- Gate evidence: re-ran `wsl bash -lc 'bash .claude/gate/lint.sh'` myself — exit 0, `Passed: 0
  failure(s), 0 warning(s) on 64 files`, matches the recorded transcript. Test-gate evidence
  (exit 1, same three pre-existing failures: restart-app.yml, tail-applog.yml,
  rollback-container.yml) is internally consistent with the documented baseline in
  `fix-generate-ip-allocation-loop.md` and none of the twelve changed files are playbooks, so I
  accepted the recorded record without re-running it.
- Value proof: the `--force` substitution is accepted. The plain command's "Nothing to do"
  result is a real `ansible-galaxy` quirk (it treats collections already present anywhere in the
  configured collections path as satisfying `-r`, even when the `-p` target is empty) — not a
  defect in `requirements.yml` or the command as written in the plan. `--force` is the minimal
  change needed to get a genuine fresh-install proof into the throwaway path, doesn't touch any
  plan file/decision/acceptance criterion, and the resulting evidence (exit 0, all four
  collections at pinned versions under `/tmp/reqcheck`) satisfies the acceptance criterion's
  actual intent (prove a fresh install resolves all four pins).
- Reflexes: no secrets, no injection, no swallowed errors in the diff (doc/config-only change).
- Scope note for korr-qa (non-blocking): `.claude/settings.local.json` also shows as modified in
  `git diff master` (accumulated Claude Code permission entries, including a broad
  `Bash(rm *)` allow-rule) — this predates this review task and is not part of the plan's twelve
  files. Recommend excluding it when staging the commit for this plan (stage named paths per repo
  convention), and flagging the `Bash(rm *)` entry separately if it warrants tightening.
- No trivia found to fix in place.

### [qa] 2026-07-06

[qa] verdict: PASS

Senior pass over the diff and run log: the four core files match the plan's verbatim blocks
(requirements.yml four exact pins in order with the verified-date header; build.yml comment
collapsed to the `-r` form with the parenthetical gone, `lint:`/`test:` byte-identical;
framework.md bullet states the reconciled source-of-truth; INDEX.md flips exactly the eight 0XX
status cells), and the D8 tier-close lands all eight README status lines citing their landing
plans. Twelve files plus this plan file; no `ansible/` code touched. Gate evidence accepted:
lint exit 0 (reviewer re-ran it), test exit 1 with only the three recorded pre-existing
failures. The `--force` addition to the value proof is sound — plain install short-circuits on
the venv's default collection path ("Nothing to do"), a satisfied-check quirk, and the forced
run proved all four pins resolve from Galaxy into an empty path at exactly the pinned versions;
a genuinely fresh clone has no pre-existing path to satisfy, so the criterion's intent is met.
Grep proof exact. The full-line churn in requirements.yml is an EOL normalization (index was
CRLF, rewrite is LF) — an improvement, verified functional by the value proof. Per the
reviewer's scope note, `.claude/settings.local.json` (session permission-cache drift, including
a `Bash(rm *)` allow entry worth a look outside this run) is deliberately NOT staged into this
commit. Clear to commit.
