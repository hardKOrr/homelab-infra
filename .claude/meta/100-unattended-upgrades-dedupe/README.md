# 100 — Dedupe unattended-upgrades implementation

**Status:** open
**Depends on:** none
**Blocks:** nothing (cosmetic), but resolves a footgun

## Problem

`ansible/tasks/bootstrap/configure-unattended-upgrades.yml` is a TODO stub, but `ansible/tasks/guest-bootstrap.yml:51-127` already implements the entire feature inline. Two sources of truth → the next contributor will edit the wrong one.

## Files

- `ansible/tasks/bootstrap/configure-unattended-upgrades.yml` — currently TODO header only
- `ansible/tasks/guest-bootstrap.yml:51-127` — actual implementation

## Approach

Two options:

**A (recommended)** — extract the implementation from `guest-bootstrap.yml` into `configure-unattended-upgrades.yml`, then `include_tasks` it from guest-bootstrap. Keeps guest-bootstrap as a thin orchestrator.

**B** — delete the stub. Document in CLAUDE.md / repo structure that unattended-upgrades is configured inline in guest-bootstrap.

Pick A. It matches the directory's "bootstrap/configure-*.yml" naming pattern and keeps guest-bootstrap readable.

## Acceptance

- [ ] `configure-unattended-upgrades.yml` contains the implementation
- [ ] `guest-bootstrap.yml` calls it via `include_tasks` (or `import_tasks`)
- [ ] Running `guest-bootstrap.yml` against a fresh LXC still produces identical state (apt cache, drop-in, marker)
