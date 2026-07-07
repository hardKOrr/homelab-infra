# remove-default-lxc-password

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** fix

**Depends on:** strip-secrets-from-guest-instance-json (both forms change how the `password` key
is handled around `lxc-create.yml`; serialize to avoid colliding edits, secrets-in-guest first)

**Spec:** .claude/specs/secrets-handling.md (no hardcoded credential defaults); review
2026-07-02, defect re-verified in tree 2026-07-06

**Meta slice:** — (003's module-arg allowlist landed; `password` is on the allowlist in
`lxc-create.yml:101` and reaches the module only when present in config)

## Goal

Eliminate the `password: changeme` default for LXC containers —
`ansible/vars/homelabinfra-defaults.yml:12` ships it to every clone of this repo, so every
container created by a user who didn't override it gets a known root password. Containers are
already provisioned with the user's SSH public key (`pubkey` in
`tasks/proxmox/lxc-create.yml`), so password auth is not needed for the platform to function;
the "defaults cover 80%" philosophy favors dropping the key entirely and letting the module
create the container key-only, if the `community.proxmox.proxmox` module accepts its absence
(verify, don't assume). If a password path is kept at all, it must be user-supplied or
generated-and-stored per specs/secrets-handling.md — never a shared literal in git. Constraint
on any generate-and-store option: Vaultwarden is not up at LXC-create time during bootstrap, and
writing a generated password anywhere else violates the secrets spec. A default deploy must
still yield root SSH access via the configured public key.

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
- **Commit:** `remove-default-lxc-password: <imperative summary>`
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
