# 101 — Guard against missing `stack` key in app template

**Status:** done
**Depends on:** 005 (schema cleanup)
**Blocks:** clean error messages on common contributor mistake

## Problem

`playbooks/apps/_template.yml:62` calls `find-or-create-host.yml` with `stack_name: "{{ app_config.stack }}"` unconditionally. If a contributor copies the template for a native LXC app but forgets to delete PATH A (Docker) and uncomment PATH B (LXC), the playbook fails with a confusing "stack is undefined" error instead of "you forgot to switch paths".

## Files

- `ansible/playbooks/apps/_template.yml:55-81` — PATH A / PATH B selection block
- `ansible/tasks/stack/find-or-create-host.yml:13-16` — current assert

## Approach

Tighten the assert in `find-or-create-host.yml` to also check that `app_config.stack` is defined, with a fail message that points at the template's PATH A/B selection. Optionally add a Play-1-level assert in the template that verifies either `stack` or the native-LXC keys are present, depending on which path is active.

## Acceptance

- [x] Running the unmodified template (with neither PATH A nor B configured) produces a clear error pointing at `_template.yml` PATH selection — assert now uses `stack_name | default('') | length > 0` so the lazy `{{ app_config.stack }}` undefined is absorbed and the fail_msg (naming PATH A/B) is what the user sees
- [x] PATH A and PATH B continue to work when correctly configured — a non-empty stack_name passes the same assert; no other behavior changed (lint + syntax gates pass)
