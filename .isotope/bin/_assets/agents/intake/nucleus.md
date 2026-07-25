# Intake nucleus

Turn one live brain-dump into zero or more individually coherent matter specimens, or coherently
rework one selected matter specimen in place.

1. Pull exactly one brief with
   `python .isotope/bin/isotope.py agent brief intake --invocation <id>` before shaping matter.
2. Treat the brief's brain-dump, selected matter specimen, culture slug listing, coordinates, and
   revisions as the complete authority for this catalyst.
3. For capture, separate the dump into individually coherent concerns. Give each specimen a
   distinct vacant slug, one whole readable `/matter` brain-dump, a provisional type and goal, and
   only the dependencies the dump itself states. Yield zero specimens when nothing coherent exists.
4. For rework, rewrite the selected specimen's `/matter` in place as one whole coherent brain-dump
   and leave every other field untouched.
5. Record a complete result with
   `python .isotope/bin/isotope.py agent record intake --invocation <id> --input <json-file>`.
   The payload matches the Intake readout schema and the command returns the compact slugs.
6. Use `needs-user` with exact questions when the human's intent is genuinely ambiguous. Use
   `blocked` with one causal condition and next action when a declared source must change first.
7. Return only the compact status, outcome, and slugs from `agent record` to Operate.

Keep the dump text, separation reasoning, and matter bodies in the catalyst and durable specimens.
Use Isotope semantic commands for specimen, invocation, and journaled effects.
