# 200 — notes

2026-07-24 — implemented and verified.

Decisions:

- **Shape B enforced end-to-end.** The task writes role-keyed data per CONTRACT §3. The three
  Shape-A `notifications.ntfy_url` readers (`check-native-updates.yml`, `restart-app.yml`,
  `configure-unattended-upgrades.yml` — CONTRACT said guest-bootstrap.yml, but the actual reader
  is the unattended-upgrades task it imports) were reconciled to `host` + `topic` in the same
  slice, per the CONTRACT §6 conflict table. CONTRACT rows marked resolved.
- **`host` semantics settled** (slice 200 owned the required-key list): every `host` in facts.yml
  is a full base URL *including scheme* (e.g. `http://192.168.1.20`). curl/uri consumers
  concatenate paths directly; consumers needing a bare hostname (shoutrrr in slice 201) strip
  the scheme. Recorded in CONTRACT §3.
- **Scalar values allowed** for `generated_facts_data` — Shape B's top-level `domain` is a
  scalar, so the assert requires "defined", not "mapping".
- **`delegate_to: localhost` + `become: false`** on both write tasks — safe to call from plays
  targeting service hosts, not just localhost plays.
- **Path default anchored to bootstrap.yml's depth** (`playbook_dir/../../config/...`);
  `generated_facts_file` override exists for callers at other depths (and for tests).

Verification: scratch playbook (4 include_tasks calls + include_vars + stat + assert) run via
`~/.venvs/homelab-ansible` in WSL with `-c local` — all assertions passed. Both gates green
(rollback-container.yml syntax failure is the pre-existing slice-502 stub, unrelated).
