# <slug>

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** feature | fix | refactor | config | docs

**Depends on:** — <slug of the form this one builds on, if any>

**Spec:** <spec path(s) this change was projected from — provenance>

**Meta slice:** — <.claude/meta/<slice> this form relates to, if any; extend that README rather
than duplicating it>

## Goal

<What this form accomplishes, in a few sentences: the concept's intent re-written as a workable
goal. A concept too large for one form is split into forms linked by `Depends on:`. Written by
sdlc-analyze.>

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
- **Commit:** `<slug>: <imperative summary>`
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
