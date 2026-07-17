# Construction nucleus

Implement one bounded planned change and record one provenance-bound Construction round.

1. Pull exactly one brief with
   `python .isotope/bin/isotope.py agent brief construction --invocation <id>` before editing.
2. Treat the brief's goal, context, change, criteria, decisions, prior findings, verification,
   gates, coordinates, and revisions as the complete authority for this catalyst.
3. Edit only ordinary repository code, tests, and declared change files. Preserve unrelated work.
4. Run the verification and gate commands needed for the change. Capture each command, exit code,
   complete output, working directory, and gate identity as evidence.
5. Record exactly one terminal round with
   `python .isotope/bin/isotope.py agent record construction --invocation <id> --input <json-file>`.
   The payload matches the Construction readout schema. The command captures the Review snapshot
   for a complete round and returns the compact durable outcome.
6. Use `needs-user` with exact questions for a genuine product decision. Use `blocked` with causal
   blockers when an external condition prevents safe completion.
7. Return only the compact status, outcome, and round identity from `agent record` to Operate.

Keep implementation details, command output, source content, and the round body in the catalyst and
durable record. Use Isotope semantic commands for specimens, operating state, invocations, and
journaled effects.
