# 602 — Rundeck job tree

**Status:** open
**Subject:** Rundeck job tree
**Related:** 601 (the job definitions this reshapes), 600 (Semaphore, no-target — not kept
in parity), 408 (the app catalog that multiplies the largest group), 204 (whose backup,
restore and reclaim jobs land in the new groups)

## Goal

Give `rundeck/jobs/` a group structure that survives the app catalog. Thirty-six jobs sit
in four flat groups, and `Apps` holds eighteen of them — over half the project — mixing
baseline platform services, ordinary applications, a hosting backend that is explicitly not
an app, and verbs that act on any app. `Maintenance` holds ten and mixes three levels of
blast radius, so the job that prints a status table and the job that destroys a volume are
six rows apart in one alphabetical column.

The change is the `group:` value and nothing else. No step, option, schedule or script is
touched, and no job is renamed. Every job carries a stable `uuid` and *Reimport Jobs* posts
with `dupeOption=update&uuidOption=preserve`, so a group change is an in-place update:
execution history, the weekly schedule on *Check Native App Updates*, and every API
reference by UUID survive it. Rollback is reverting the commit and re-running the import.

The top level sorts by verb — `Deploy`, `Operate`, `Recover`, `Setup` — because that is the
question an operator arrives with, and because subject is the axis that grows. Slice 408
adds application rows in three batches; they land under `Deploy/` without the top level
moving. `Recover` exists so that the four jobs which can lose data, plus the one that can
lose the credential path to everything else, are not reachable by mis-clicking one row
above the job that was wanted.

## Remaining

- [ ] Target tree agreed, including the three judgment calls recorded in notes.md
      (*Remove App*, *Wire Media Stack*, and whether `Setup` earns a fourth root).
- [ ] `group:` rewritten in all 36 files under `rundeck/jobs/`, one line per file.
- [ ] `AGENTS.md` **UI Job Structure (Rundeck)** section rewritten in the same commit. It
      is the written form of this tree, and it already promises a `Backend` group that does
      not exist in `rundeck/jobs/`.
- [ ] *Reimport Jobs* run against the live project, reporting `36 imported, 0 failed`, with
      the console confirming no job was duplicated and no group was orphaned.

## Links
- `rundeck/jobs/*.yaml` — the `group:` value on each job is the whole of the change
- `rundeck/jobs/reimport-jobs.yaml` — the rollout and rollback path
- `AGENTS.md` — the UI Job Structure section this slice keeps true
- notes.md — the target tree, the reasoning, the verified migration mechanics, and what was
  deliberately left alone
