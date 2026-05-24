# 102 — Restart/tail playbook assert ordering

**Status:** open
**Depends on:** none
**Blocks:** nothing critical — better UX on missing param

## Problem

`playbooks/maintenance/restart-app.yml` and `tail-applog.yml` both set `hosts: "{{ instance }}"` at the play level. The "assert instance is defined" task is `delegate_to: localhost` inside that play. If `instance` is not passed, Ansible errors trying to resolve the hosts pattern before the assert runs — the user sees an unhelpful inventory error rather than the "pass -e instance=..." message.

## Files

- `ansible/playbooks/maintenance/restart-app.yml:10-25`
- `ansible/playbooks/maintenance/tail-applog.yml:11-21`

## Approach

Split each playbook into two plays:

1. `hosts: localhost` — assert `instance` is defined.
2. `hosts: "{{ instance }}"` — do the work.

The assert in play 1 fails fast with the friendly message before Ansible tries to resolve play 2's hosts pattern.

## Acceptance

- [ ] Running `restart-app.yml` without `-e instance=...` fails with the friendly assert message, not an inventory error
- [ ] Same for `tail-applog.yml`
- [ ] Successful runs (with `-e instance=foo`) still work end-to-end
