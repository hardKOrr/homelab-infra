# sdlc-red-test-gate-maintenance-playbooks

Concept brain-dump for the architect / sdlc-analyze. Discovered during the
`fix-adhoc-playbook-env` run (2026-07-06), out of that form's scope ("no playbook changes").

## Observation

`.claude/gate/test.sh` exits 1 on master — the base gate is red for every run, and has been
silently absorbing findings. Three pre-existing defects, reproduced identically with master's
`ansible.cfg` (so unrelated to the callback fix):

1. `ansible/playbooks/maintenance/restart-app.yml:10` — `hosts:` interpolates `{{ instance }}`,
   undefined at `--syntax-check` time (no `-e instance=` in the gate) → hard ERROR, not a
   warning.
2. `ansible/playbooks/maintenance/tail-applog.yml:11` — same pattern, same ERROR.
3. `ansible/playbooks/stacks/rollback-container.yml` — empty file (TODO placeholder) →
   `ERROR! Empty playbook, nothing to do`.

## Why it matters

- A red base gate destroys the gate's signal: construction rounds can neither prove "exit 0" nor
  distinguish their own breakage from the ambient failures. The `fix-adhoc-playbook-env`
  acceptance had to waive its "test.sh → exit 0" criterion with a human-approved deviation.
- The two `hosts:` defects break the one-click jobs themselves, not just the gate — a Semaphore
  run without the `instance` param dies the same way.

## Candidate direction (architect's call)

- `hosts: "{{ instance | default('none') }}"` (or an assert-first guard play) makes the
  playbooks syntax-checkable while keeping the required-param contract — see
  specs/framework.md "Errors" (assert-first with friendly fail_msg).
- `rollback-container.yml` needs at least a minimal valid play (its real implementation is a
  day-2 backlog item — cross-reference `.claude/meta/` before creating a new slice; do not
  duplicate an existing one).
- Consider whether test.sh should hard-fail on an empty playbook or the file should simply not
  exist until implemented.
