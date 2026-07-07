# make-stack-host-docker-ready

<!-- The slug is the form's identity: this file's full name (<slug>.md), the lead of the run
branch (after an optional type segment, e.g. feat/<slug>), and the lead of every commit subject
this form produces. Stage owners: sdlc-analyze writes the requirements half (Type / Depends on /
Spec / Goal); sdlc-build writes the operation half (Context / Acceptance criteria / Plan /
Decisions / Verification); the construction loop grows the Run log. -->

**Type:** fix

**Depends on:** modernize-docker-apt-repo (this form makes the create path apply the docker
role; that role must install cleanly on current Debian first)

**Spec:** .claude/specs/one-click-idempotent.md (one click must yield a running app — no manual
docker-install step); review 2026-07-02, defects re-verified in tree 2026-07-06

**Meta slice:** .claude/meta/103-find-or-create-host-docs (open — documents this file's state
machine; extend that README rather than duplicating it)

## Goal

Make `ansible/tasks/stack/find-or-create-host.yml` produce a host that can actually run Docker
apps on the first deploy targeting a new stack. Three gaps in the create path:

1. **Invalid hostname** — line 58 sets `hostname: stack_name`, but stack names use underscores
   (`media_stack`) and underscores are invalid in hostnames; `pct create` rejects them. The
   hostname needs the hyphenated form while the Proxmox tag keeps the verbatim underscore form —
   the inventory group `tag_<stack_name>` is how the host is found on subsequent deploys.
2. **Missing container features** — Docker-on-LXC needs keyctl (nesting comes from
   `vars/homelabinfra-defaults.yml`). The feature-merge logic already exists in
   `playbooks/docker/create-docker-host.yml:31-44` (handles both mapping and list shapes of
   `proxmox.lxc.features`) and must also apply on the find-or-create create path.
3. **Docker never installed** — nothing applies the `docker` role to a freshly created stack
   host before Play 2 of `playbooks/apps/_template.yml` runs
   `community.docker.docker_compose_v2`. find-or-create runs in Play 1 on localhost and cannot
   run the role on the guest itself; the handoff to Play 2 is via `add_host` hostvars (the
   sanctioned cross-play channel), so either it marks the new host for a conditional role
   application in Play 2, or Play 2 applies `roles/docker` idempotently before the app role for
   Docker apps.

The existing-host path must stay untouched except as needed for group/tag lookup consistency.

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
- **Commit:** `make-stack-host-docker-ready: <imperative summary>`
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
