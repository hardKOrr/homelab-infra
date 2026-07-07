# fix-adhoc-playbook-env

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** fix

**Depends on:** —

**Spec:** .claude/specs/framework.md ("Language & toolchain", "Tests"); discovered empirically
2026-07-04 running a value-proof playbook in the gate venv; defect re-verified in tree 2026-07-06

**Meta slice:** — (meta 007 owned the collection pins and closed 2026-07-06; the old
"coordinate, don't collide" note is obsolete — pins are landed and out of scope here)

## Goal

Make real (non-syntax-check) `ansible-playbook` runs work out of the box in the gate venv
(`~/.venvs/homelab-ansible`). Two defects block every genuine run today:

1. `ansible/ansible.cfg:6` sets `stdout_callback = yaml`, which resolves to
   `community.general.yaml` — **removed in community.general 12.0.0**. Ansible dies on line one
   with "[DEPRECATED]: community.general.yaml has been removed. The plugin has been superseded by
   the option `result_format=yaml` in callback plugin ansible.builtin.default from ansible-core
   2.13 onwards." The two gates pass only because ansible-lint and `--syntax-check` never engage
   the stdout callback. This is shipped config, so it equally breaks any user whose control node
   has community.general ≥ 12. Keep YAML-shaped output (deliberate repo choice) via the supported
   mechanism: `ansible.builtin.default` with `result_format=yaml`, option name verified against
   the installed ansible-core's `ansible-doc -t callback ansible.builtin.default`.
2. `.claude/gate/requirements-dev.txt` lacks `netaddr`, so every `ansible.utils` IP filter the
   repo depends on (`ipaddr('size')` / `nthhost` in `ansible/tasks/network/generate-ip.yml`,
   `ipaddr('prefix')` in `ansible/tasks/proxmox/lxc-create.yml`) raises "Failed to import the
   required Python library (netaddr)" at runtime. It was installed manually into the venv on
   2026-07-04 as a stopgap; the requirements file must record it so a fresh venv bootstrap
   (procedure in the `.claude/build.yml` comment block) works.

No change to playbooks, tasks, roles, or collection pins.

## Context

<Narrated, self-contained: what this form needs to know, projected from the architecture and
specs so downstream never re-reads the sources. For this repo, always state which variable
namespaces the change touches (homelabinfra_config / _instance / _infra) and which plays it runs
in (localhost provision / guest deploy / localhost wire) — the cross-play seam is where changes
here go wrong. Written by sdlc-build.>

## Acceptance criteria

- <Checkable at acceptance time — from the diff, the tests, or observed behavior. Encodes the
  in-scope spec clauses; references only real build.yml commands. Written by sdlc-build; checked
  by sdlc-build at acceptance.>

## Plan

<The form broken into numbered single-changes — one reviewable change and one commit each, every
file named, every decision made. Each change is one sdlc-construction firing. Written by
sdlc-build.>

### change 1 — <short name>

- **Files:** <files to touch>
- **Steps:** <the pseudo-code of the change>
- **Tests:** <the tests to write first; while the repo has no molecule/unit layer, state the
  worked example or ad-hoc localhost play that proves the change — see specs/framework.md
  "Tests">
- **Commit:** `fix-adhoc-playbook-env: <imperative summary>`
- **Model:** sonnet | opus  <!-- omit for sonnet; opus for an intricate change -->

## Decisions

- <Question> → <the decision taken, and why. Resolved, not open: construction is left no decision
  to make. Written by sdlc-build; one it cannot resolve rides up to the human before the run
  starts, never down to construction.>

## Verification

- <How to prove the whole form landed — integration-level, beyond any one change's tests. Written
  by sdlc-build; run at acceptance.>

## Run log

<Append-only, one block per round — construction and build grow it, never rewrite it. Construction
appends a `[construction]` block per round — what changed, files touched, each gate command with
its exit code and output tail (the evidence build verifies) — and never a verdict. Build appends a
`[build] verdict: PASS|CHANGES` line per round and one `[accept] verdict: PASS|CHANGES` at
form-level acceptance. The `[build]` / `[accept]` marker and its verdict sit on **one line**,
exactly as in the example below — guard-runlog rejects any other shape. Empty until the first
round.>

<!-- example shape (delete when the first real round lands):
### change 1 / round 1
[construction] what changed, files touched, notes for build; may flag "(decision needed: …)"
 - test: `wsl bash -lc 'bash .claude/gate/test.sh'` → exit 0 (all playbooks syntax-OK)
 - lint: `wsl bash -lc 'bash .claude/gate/lint.sh'` → exit 0
[build] verdict: CHANGES
 - finding: what failed, where, why — actionable without re-review

### change 1 / round 2
[construction] what changed in response
 - test: `wsl bash -lc 'bash .claude/gate/test.sh'` → exit 0
[build] verdict: PASS
 - committed: <slug>: <change summary>

### acceptance
[accept] verdict: PASS
 - verification: <what was run and observed>
-->
