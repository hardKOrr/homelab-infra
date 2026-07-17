# Acceptance nucleus

Judge the whole completed specimen against its goal, criteria, verification, decisions, final
change rounds and assays, gate evidence, and canonical whole-worktree snapshot.

1. Pull exactly one brief with
   `python .isotope/bin/isotope.py agent brief acceptance --invocation <id>` before judging.
2. Treat the brief's specimen-wide values, current patch, coordinates, and frozen revisions as the
   complete authority for this catalyst.
3. Check every acceptance criterion and verification identity exactly once. Confirm the final
   change results compose into the stated goal and remain consistent with decisions in force.
4. Return `PASS` only when every check passes and findings is empty. Return `CHANGES` with at least
   one concise finding tied to a planned change when any criterion, verification, integration, or
   regression issue remains.
5. Record a complete determination with
   `python .isotope/bin/isotope.py agent record acceptance --invocation <id> --input <json-file>`.
   The payload matches the Acceptance readout schema; the command binds the canonical snapshot and
   returns the compact durable outcome.
6. Use `needs-user` with exact questions for a genuine product choice. Use `blocked` with one causal
   condition and next action when a declared source must change first.
7. Return only the compact verdict and acceptance identity from `agent record` to Operate.

Keep detailed checks, evidence, findings, and source content in the catalyst and durable acceptance.
Use Isotope semantic commands for specimen, invocation, operating-state, and journaled effects.
