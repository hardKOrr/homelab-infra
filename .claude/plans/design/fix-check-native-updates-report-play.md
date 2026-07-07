# fix-check-native-updates-report-play

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** fix

**Depends on:** fix-adhoc-playbook-env (the localhost report play is directly provable with an
ad-hoc run once the venv's stdout callback works)

**Spec:** .claude/specs/provider-noop-wiring.md (homelabinfra_infra is the only source of service
endpoints; a missing provider is a no-op, not an error); review 2026-07-02, defect re-verified in
tree 2026-07-06

**Meta slice:** —

## Goal

Make the weekly native-app update-check notification actually able to fire. Two defects in
`ansible/playbooks/maintenance/check-native-updates.yml`:

1. The "Report available updates" play references
   `homelabinfra_infra.notifications.ntfy_url` (line 54), but nothing in that play loads
   `config/.generated/facts.yml` into `homelabinfra_infra`. The Ntfy task is gated
   `when: _updates_available | length > 0`, so the playbook succeeds only when it has nothing to
   say and fails on an undefined variable exactly when updates exist. The working pattern lives
   in `playbooks/maintenance/restart-app.yml:33-36` (`include_vars` of the facts file with
   `name: homelabinfra_infra`); the notify must also degrade to a silent no-op when the facts
   file or notifications provider is absent, per specs/provider-noop-wiring.md.
2. Play 1 targets `hosts: tag_homelab_infra` (line 10), but the tag is `homelab-infra` and the
   inventory keyed_groups config (`ansible/inventory/proxmox.yml`, prefix `tag`, separator `_`)
   does not convert the hyphen — the emitted group is likely `tag_homelab-infra` depending on
   `TRANSFORM_INVALID_GROUP_CHARS`. The host pattern must match the group name the
   `community.proxmox` plugin actually emits, verified rather than assumed, and stated in a
   comment.

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
- **Commit:** `fix-check-native-updates-report-play: <imperative summary>`
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
