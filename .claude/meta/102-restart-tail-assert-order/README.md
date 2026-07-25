# 102 — Restart/tail playbook assert ordering

**Status:** done
**Depends on:** none
**Blocks:** nothing critical — better UX on missing param

## Problem

`playbooks/maintenance/restart-app.yml` and `tail-applog.yml` both set `hosts: "{{ instance }}"` at the play level. The "assert instance is defined" task is `delegate_to: localhost` inside that play. If `instance` is not passed, Ansible errors trying to resolve the hosts pattern before the assert runs — the user sees an unhelpful inventory error rather than the "pass -e instance=..." message.

Second defect in the same playbook (review 2026-07-02): `restart-app.yml`'s Notify play
(lines 27-47) is a separate play on localhost, so it still runs when the restart play failed on
its hosts — and always sends "was restarted manually" success text. The header comment promises
"notifies on success or failure"; the notify must check the restart result (e.g. via
`hostvars[...]._restart`) and word the message accordingly, or be skipped on failure.

## Files

- `ansible/playbooks/maintenance/restart-app.yml:10-25`
- `ansible/playbooks/maintenance/tail-applog.yml:11-21`

## Approach

Split each playbook into two plays:

1. `hosts: localhost` — assert `instance` is defined.
2. `hosts: "{{ instance }}"` — do the work.

The assert in play 1 fails fast with the friendly message before Ansible tries to resolve play 2's hosts pattern.

## Acceptance

- [x] Running `restart-app.yml` without `-e instance=...` fails with the friendly assert message, not an inventory error — assert moved to a leading localhost play; the work play's hosts pattern also gained `| default('_instance_unset')` so `--syntax-check` (which templates play hosts) passes too
- [x] Same for `tail-applog.yml`
- [x] Successful runs (with `-e instance=foo`) still work end-to-end — work plays unchanged apart from the removed inline assert; lint + syntax gates pass
- [x] (second defect) Notify play now reads `hostvars[instance]._restart` and words the Ntfy message/priority by outcome; it also tolerates missing facts.yml and no-ops unless the ntfy provider is configured, per the provider-noop philosophy
