# Meta — the backlog

This directory tracks **why** a change exists and **what is left on it**. Git tracks the
code; this tracks intent, scope and ordering.

## Layout

```
INDEX.md              # the work queue — a table, always. Start here.
LESSONS.md            # durable knowledge that outlived its slice. Prose lives here.
README.md             # this file: slice shape and workflow
NNN-short-slug/       # one live slice
  README.md           #   spec — four fixed sections, see below
  notes.md            #   optional: session narrative, dead ends (append-only)
done/NNN-short-slug/  # finished slices, archived verbatim for provenance
```

Numbering: `NNN` — first digit is **tier** (0 = highest priority, 6 = lowest), last two
digits are order within the tier.

## One fact, one home

The rule that keeps this directory readable. When the same fact is written in three places,
two of them go stale silently.

| Fact | Home |
|---|---|
| What is left on a slice | that slice's `README.md` **Remaining** section |
| What to work on next | `INDEX.md` |
| A lesson that applies beyond its slice | `LESSONS.md` |
| Registry key shapes, merge order, config schema | `ansible/vars/CONTRACT.md` |
| What happened in a work session | that slice's `notes.md` |

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

1. Pick a slice from `INDEX.md` **Start here**, or an `open` row with no unmet dependency.
2. Work it. Append discoveries to `notes.md`.
3. Code written, both gates green → `built`. Update the slice's **Remaining** and its
   INDEX row in the same edit.
4. Acceptance observed live → `done`. Tick the boxes, `git mv` the folder into `done/`,
   delete its INDEX row, update the counts in the INDEX header.
5. A lesson that would change how another slice is worked → add it to `LESSONS.md`.

`built` is the honest resting state for anything touching Proxmox: gate-green code whose
acceptance has not been watched happening. Do not flip to `done` on a syntax check — see
"Green is not working" in `LESSONS.md`.
