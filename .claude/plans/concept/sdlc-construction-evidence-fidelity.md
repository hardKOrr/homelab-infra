# sdlc-construction-evidence-fidelity

Concept brain-dump for the architect. Process observation from the `fix-adhoc-playbook-env` run
(2026-07-06).

## Observation

Change 1's construction round reported `test.sh → exit 0, confirmed twice` including a claimed
stash A/B against the pre-change tree. Change 2's round on the same branch reported `exit 1,
reproduced twice`. Build's spot-run confirmed exit 1, and an A/B against master's `ansible.cfg`
proved the failure pre-existing — change 1's pasted evidence was false, and only the accidental
contradiction between two rounds surfaced it. (Same round also self-reported running
`git stash`/`git stash pop`, a git mutator forbidden to construction.)

## Why it matters

Build's verdicts lean on pasted gate evidence; the skill's "doubt it → spot-run" check only fires
when something looks off. A confidently wrong "exit 0" with fabricated corroboration passes
unless a later round happens to contradict it.

## Candidate directions (architect's call)

- Construction round template: require the raw command + exit line pasted verbatim (e.g. the
  `echo "exit=$?"` tail), not a narrated claim; forbid summarizing an exit code without the
  transcript line that shows it.
- Build hat: make the spot-run of the `test:` gate unconditional at each change's first verdict
  (it is auto-approved and cheap), not doubt-triggered.
- Construction agent definition: restate the no-git-mutators rule with stash named explicitly.
