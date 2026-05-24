# 504 — Wire media stack playbook

**Status:** open
**Depends on:** 300-305 (wiring tasks); apps in the media stack should exist first, but the playbook can be authored before
**Blocks:** "click wire stack" Semaphore job for the media stack

## Problem

CLAUDE.md describes `wire-<stack>.yml` playbooks for app-to-app wiring within a stack (Sonarr → Prowlarr, Radarr → qBittorrent, etc.). None exists.

This is the first stack playbook — others can be cloned from it.

## Files

- `ansible/playbooks/stacks/wire-media-stack.yml` — create
- `ansible/tasks/app-wiring/` — new directory for per-pair wiring tasks (e.g. `sonarr-to-prowlarr.yml`)

## Approach

The playbook is idempotent and runnable any time. It only does app-to-app wiring (the Caddy/Authentik/Kuma wiring is per-app and already happens in each app's deploy).

Structure:
```yaml
- hosts: localhost
  tasks:
    - name: Wire Sonarr → Prowlarr
      include_tasks: ../../tasks/app-wiring/sonarr-to-prowlarr.yml
      when: 'tag_sonarr' in groups and 'tag_prowlarr' in groups

    - name: Wire Radarr → Prowlarr
      include_tasks: ../../tasks/app-wiring/radarr-to-prowlarr.yml
      when: ...

    - name: Wire Sonarr → qBittorrent
      ...

    - name: Notify
      ...
```

Each `app-wiring/*.yml` task:
- Reads connection details from `homelabinfra_infra.<app>` (must be populated by app deploys via write-generated-facts — or via Vaultwarden lookup for tokens).
- Calls the source app's API to register the target as a downloader / indexer / etc.
- Idempotent (check-before-create).

For v1, this is mostly scaffolding — start with one real connection (Sonarr ↔ Prowlarr) and the framework for adding more.

## Acceptance

- [ ] Running wire-media-stack on an empty stack is a no-op (no failures)
- [ ] After deploying Sonarr + Prowlarr, running wire-media-stack registers Prowlarr in Sonarr
- [ ] Re-run is idempotent
- [ ] Ntfy notification "Media stack wired: N connections confirmed" fires
