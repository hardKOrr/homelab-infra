# strip-secrets-from-guest-instance-json

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** fix

**Depends on:** —

**Spec:** .claude/specs/secrets-handling.md (no secret may be persisted into a managed guest);
review 2026-07-02, defect re-verified in tree 2026-07-06 (meta 003's module-arg allowlist has
since landed but does not touch the persisted copy)

**Meta slice:** — (003 closed; only the sanitization of the persisted copy remains — do not
restructure the module call)

## Goal

Stop writing the Proxmox API token and LXC root password into every provisioned guest.
`ansible/tasks/proxmox/lxc-create.yml:145-154` pipes `homelabinfra_instance.lxc | to_nice_json`
into the new container at `/root/home/homelabinfra_instance.lxc.json`;
`ansible/tasks/proxmox/vm-create.yml:165-174` does the same via `qm guest exec` for VMs. That
dict is the full instance dict built at the top of each file and contains `api_token_secret`
(full Proxmox API control), `api_token_id`, `pubkey`, and for LXC `password` (root password —
`changeme` by default). The file is written with default umask (world-readable). Required
outcome: the persisted JSON contains no API token, password, or SSH key material in either file;
the in-guest file lands at a sane path (`/root/home/` is almost certainly a typo for `/root/`;
nothing else in the repo reads this file yet — confirm by grep before choosing) with 0600
permissions; the module call and the create/wait behavior are otherwise unchanged.

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
- **Commit:** `strip-secrets-from-guest-instance-json: <imperative summary>`
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
