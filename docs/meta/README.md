# Meta — specifications and acceptance evidence

GitHub Issues is the live work queue: priority, assignment, and status live there — see
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) for the issue-to-PR lifecycle. This
directory holds what an issue is too short-lived to carry: detailed specifications,
decision records, and acceptance evidence. Git tracks the code; this directory records
intent, scope, and what remains unverified for the slices that back an open issue.

## Layout

```
INDEX.md              # maps open issues to the slices that implement them
INDEX-ARCHIVE.md      # superseded long-form index; historical evidence only
LESSONS.md            # durable knowledge that outlived its slice. Prose lives here.
README.md             # this file: slice shape and workflow
NNN-short-slug/       # one live slice
  README.md           #   spec — four fixed sections, see below
  notes.md            #   optional: session narrative, dead ends (append-only)
done/NNN-short-slug/  # finished slices, archived verbatim for provenance
no-target/NNN-slug/   # built slices with no selectable provider or deploy target
```

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier.

## One fact, one home

The rule that keeps this directory readable. When the same fact is written in three places,
two of them go stale silently.

| Fact | Home |
|---|---|
| What to work on next, and its priority | GitHub Issues |
| The detailed spec an issue implements | that slice's `README.md` **Goal** and **Links** |
| What remains to accept on a slice | that slice's `README.md` **Remaining** section |
| A lesson that applies beyond its slice | `LESSONS.md` |
| Registry key shapes, merge order, config schema | `ansible/vars/CONTRACT.md` |
| What happened in a work session | that slice's `notes.md` |
| The issue-to-PR lifecycle | `../../CONTRIBUTING.md` |

Anywhere else, link — do not restate.

## Slice README template

Four sections, in this order. No "Approach" section: a plan that has shipped is
indistinguishable from a plan that has not, and stale approach text is what made the old
slices unreadable. Design discussion goes in `notes.md`, where its date is visible.

```markdown
# NNN — Short title

**Status:** open | built | done
**Subject:** the thing this is about, matching INDEX's subject map
**Related:** NNN (what that slice covers), ... or none

## Goal
One paragraph. What this slice delivers, in the shape it actually shipped.

## Remaining
- [ ] the verifiable outcome still outstanding
- [x] met 2026-08-08 — how it was observed

## Links
- `path/to/file.yml` — what this slice owns there
- notes.md — session narrative
```

When the shipped mechanism satisfies a criterion's *intent* but not its literal wording
(the design changed underneath it), tick it and say what shape it was met against. Do not
leave it unticked forever.

## Workflow

GitHub Issues owns current priority — see [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).
The issue and slice acceptance are different: the issue describes repository work; a slice's
**Remaining** boxes can require live lab or third-party evidence. Do not turn an acceptance
observation into an unplanned hardening sidequest.

1. Implement the linked GitHub issue. If it references no `docs/meta/` slice and the work is
   more than trivial, create one and link it from the issue.
2. Append discoveries to the `notes.md` of each affected slice.
3. When code is complete and both gates are green, mark the slice `built`, update its
   **Remaining** evidence, and update the `INDEX.md` row for the issue in the same change.
4. When all required acceptance is observed, mark the slice `done`, move it into `done/`, remove
   its `INDEX.md` row, and close the issue per `CONTRIBUTING.md`.
5. Add a lesson to `LESSONS.md` only when it changes how another slice should be worked.

`built` is the honest resting state for anything touching Proxmox: gate-green code whose
acceptance has not been watched happening. Do not flip to `done` on a syntax check — see
"Green is not working" in `LESSONS.md`.
