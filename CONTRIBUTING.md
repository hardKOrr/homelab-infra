# Contributing — the issue-to-PR operating contract

This document defines how work enters this repository and how it leaves as a merged pull
request. It applies equally to a human contributor and to an AO worker session.

## GitHub Issues is the live work queue

**GitHub Issues is the authoritative source for what to work on next and what state it is
in.** Priority, assignment, and status live on the issue — its labels, its open/closed
state, and its comments — not in a Markdown table.

[`docs/meta/`](docs/meta/README.md) is not the queue. It remains the home for detailed
specifications, decision records, and acceptance evidence — the "what exactly does this
mean and how do we know it shipped" that is too long-lived and too structured for an issue
body. When work has a `docs/meta/NNN-slug/` slice, its issue links to that slice instead of
restating it, per the one-fact-one-home rule in [`docs/meta/README.md`](docs/meta/README.md).
The meta directory does not maintain an issue table; use GitHub Issues for current work and
status.

### AO tracker intake is read-only

AO can read GitHub Issues to start a worker session against one, but AO's intake does not
write closure state back to GitHub. **An issue closes only two ways: a merged pull request
whose body says `Closes #<issue>`, or a human closing it directly on GitHub.** A worker
session finishing its local checks does not close the issue by itself — the PR does, on
merge.

That closure is also required to prevent duplicate intake: an open issue that is still
assigned to `hardKOrr` is eligible for AO tracker intake, so AO can pick it again after its
implementation PR has merged and start a second worker for work that is already complete.
The implementation PR must therefore close the issue when its repository scope and
synthetic/gate acceptance are complete. This is not a claim that live-lab acceptance has
already happened; that evidence belongs to the follow-up observation issue described below.

## Issue templates

Filing an issue picks one of four forms under `.github/ISSUE_TEMPLATE/`:

| Template | Use it for |
| --- | --- |
| Implementation work | New behavior, a new application, a change to provisioning or operating logic |
| Defect | Something the platform does today that is wrong |
| Documentation | A README, spec, or contract that is missing, wrong, or out of date |
| Live-lab observation | Recording what actually happened on the running lab, separate from repository work |

Every form asks for the same eight fields: **Goal, Scope, Exclusions, Acceptance criteria,
Dependencies, Verification, Live-system authority, Recovery needs.** Live-system authority
and recovery needs exist because [`AGENTS.md`](AGENTS.md) requires identifying the exact
target and recovery behavior before a disruptive action — an issue that touches a live
guest, container, or Proxmox resource states that up front, not partway through review.

## Worker lifecycle

1. **Intake.** Read the issue in full, including its labels and any linked
   `docs/meta/<NNN-slug>/README.md` spec. Treat the spec, where one exists, as the detailed
   source of truth; the issue is the scope and status pointer to it.
2. **Confirm scope.** The issue's Goal, Scope, and Exclusions sections bound the work. If
   they are ambiguous or the linked spec disagrees with the issue, resolve that before
   writing code — ask rather than guess when only the issue author can decide.
3. **Work on a focused branch,** scoped to the one issue. Keep reusable behavior in roles
   or task files per [`AGENTS.md`](AGENTS.md); keep the change to what the issue's Scope
   and Exclusions describe.
4. **Verify.** Run the checks the issue's Verification section names, at minimum
   `bash gate/lint.sh` and `bash gate/test.sh` (see [`gate/README.md`](gate/README.md)).
   Capture the result — it goes in the PR, not just in a local terminal.
5. **Commit** in focused, conventional-style commits.
6. **Push** the branch and **open a pull request** using the PR template. Fill in every
   section, especially Verification evidence and Live-lab status.
7. **Link the issue.** The PR body includes `Closes #<issue>` when this PR fully satisfies
   the issue's repository scope and synthetic/gate acceptance, so GitHub closes the issue
   on merge. Use `Closes #<issue>` even when live-lab observation is deliberately deferred:
   record that deferral in the PR's **Live-lab status** section and in the linked
   `docs/meta/<NNN-slug>/README.md` **Remaining** section, then carry it forward in a
   follow-up Live-lab observation issue. That observation issue is self-contained: it names
   the source implementation issue and links to the exact `docs/meta/...` **Remaining**
   criterion when one exists. There is no umbrella acceptance lane.
   Reserve `Refs #<issue>` for a PR that only partially implements the issue's repository
   scope, when more repository work is still required on that same issue. Never close an
   issue by hand when a PR is meant to close it; let the merge do it.
8. **Claim before continuing.** If a session is picking up an existing PR rather than
   opening a new one, claim it first (`ao session claim-pr <pr-ref>`) so two sessions do
   not push conflicting fixes to the same branch.
9. **Handoff.** If a session ends before an issue's acceptance criteria are fully met,
   leave state on the PR or issue itself — a comment naming what verified, what remains,
   and why — rather than in a chat transcript the next session cannot see.

## Gate-green vs. live-lab acceptance

These are different claims and this repository does not conflate them, per
[`docs/meta/README.md`](docs/meta/README.md)'s `built` vs. `done` distinction:

- **Gate-green** means `bash gate/lint.sh` and `bash gate/test.sh` pass against the
  changed code. It confirms the repository is internally consistent. It does not confirm
  the change behaves correctly against live Proxmox, a real guest, or a real schedule
  firing.
- **Live-lab acceptance** means the behavior was watched happening on the running lab —
  a scheduled window fired, a guest recovered, a job produced the expected result.

A PR that is gate-green and fully satisfies the issue's repository scope should use
`Closes #<issue>` even when live-lab evidence is deliberately deferred. Say so plainly in
the PR's **Live-lab status** section, record what remains in the linked slice's
`docs/meta/<NNN-slug>/README.md` **Remaining** section, and carry that evidence into a
self-contained follow-up Live-lab observation issue. The observation issue names its source
implementation issue and links to the exact **Remaining** criterion when one exists; it is
not nested under an umbrella acceptance lane. That observation issue, not the implementation
PR's closure, carries live-lab acceptance.

This preserves the `built` vs. `done` distinction in [`docs/meta/README.md`](docs/meta/README.md):
closing the implementation issue means the repository work is built and gate-green, not
that it has been accepted on the live lab. Mark the slice `done` only after the required
observation is recorded. Use `Refs #<issue>` only when the PR leaves more repository
implementation work for that same issue. Do not expand an observation into unplanned
implementation work to make an issue closeable; open a separate Defect issue for anything
the observation reveals.

Workers may gather that observation themselves through the approved Rundeck API workflow.
See [`docs/live-lab.md`](docs/live-lab.md) for authentication, job selection, idempotence
runs, output capture, and job-definition reimport. The guide is an evidence-collection
how-to; the status choices and issue flow above remain the acceptance contract.
