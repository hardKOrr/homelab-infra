# Expression nucleus

Express one accepted specimen's goal, landed outcome, and decisions in force into its declared
Atlas documentation targets, then record the expression evidence.

1. Pull exactly one brief with
   `python .isotope/bin/isotope.py agent brief expression --invocation <id>` before writing.
2. Treat the brief's goal, outcome packet, decisions in force, current target sections,
   coordinates, and revisions as the complete authority for this catalyst.
3. Rewrite each declared target section between its existing markers so the document states the
   current understanding: what the repository now does, why, and under which decisions. Write for
   the human reader; history stays with Git and the stable specimen.
4. Edit only the declared target files and keep every section marker intact. Recording verifies
   that the worktree changed nowhere else since acceptance.
5. Record a complete result with
   `python .isotope/bin/isotope.py agent record expression --invocation <id> --input <json-file>`.
   The payload matches the Expression readout schema; the command binds each target's resulting
   revision into durable evidence and returns the compact touched targets.
6. Use `needs-user` with exact questions when the human owns a wording or scope choice. Use
   `blocked` with one causal condition and next action when a declared source must change first.
7. Return only the compact status, expressed outcome, and touched targets from `agent record` to
   Operate.

Keep drafting and document bodies in the repository docs and durable evidence. Use Isotope
semantic commands for specimen, invocation, operating-state, and journaled effects.
