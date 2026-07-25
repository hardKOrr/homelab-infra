# Meta — Change History

This directory tracks **the why and the plan** for changes to this repo. Git tracks the code; this tracks the intent, scope, and ordering of work-in-progress slices.

## Layout

One folder per change, numbered sequentially. Each folder is a self-contained slice — small enough to load fully into a fresh context, big enough to deliver a working unit.

```
NNN-short-slug/
  README.md       # spec: problem, files, acceptance, dependencies, status
  notes.md        # optional: rolling notes, decisions, dead ends (append-only)
```

## Slice README template

```markdown
# NNN — Short title

**Status:** open | built | done | abandoned
**Depends on:** (NNN, ...) or none
**Blocks:** (NNN, ...) or none

## Problem
One paragraph. What is broken or missing.

## Files
- path/to/file.yml:line — what changes here

## Approach
Bullets. Concrete steps.

## Acceptance
- [ ] verifiable outcome 1
- [ ] verifiable outcome 2
```

## Workflow

1. Pick a slice with status `open` and no unmet dependencies.
2. Work the slice. Append to `notes.md` as you discover things.
3. When the code is written and both gates are green, flip to `built`.
4. When acceptance is observed on the live lab, flip to `done` and tick the boxes.
5. If abandoned, mark `abandoned` with a note on why.

`built` is the honest resting state for anything that touches Proxmox: gate-green code
whose acceptance criteria have not been watched happening. Do not flip to `done` on the
strength of a syntax check.

## Index

See [INDEX.md](INDEX.md) for the live list and dependency graph.
