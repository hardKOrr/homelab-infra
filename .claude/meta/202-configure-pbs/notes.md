# 202 — notes

2026-07-24 — implementation complete; slice stays in-progress until live verification.

Key decisions / deviations from the original spec:

- **"Add PVE as remote" dropped** — that concept doesn't exist in PBS (remotes are other PBS
  servers, for sync). Implemented the real topology: PBS-side datastore + retention +
  notification target; PVE-side `pbs`-type storage entry + vzdump backup job. README approach
  section rewritten to match.
- **Tag-based selection resolved at configure time.** PVE cluster backup jobs can't filter by
  tag, so the job is created with the explicit vmid list of `homelab-infra`-tagged guests
  (from `/cluster/resources`). Bootstrap runs PBS last, so all baseline guests exist by then.
  Re-running the task (or bootstrap) refreshes the list — consistent with fire-and-forget,
  but note: a guest deployed *after* bootstrap is not auto-added to the backup job. Candidate
  follow-up: refresh the vmid list from the app-deploy wiring step.
- **Idempotency by list-then-create**: datastore, webhook target, matcher, PVE storage each
  checked by name before POST; the backup job is found by its `managed by homelab-infra`
  comment marker and PUT-updated (vmid list + schedule) when present.
- **Retention lives on the PBS datastore** (`keep-last/daily/weekly/...` from
  `infrastructure.backups.retention`, `keep_x` → `keep-x`), not on the PVE job — one place,
  PBS prunes autonomously.
- **Schedule keywords** `daily` → `02:00`, `weekly` → `sun 02:00`; anything else passes
  through as a PVE calendar event.
- **Facts written**: `backups: {instance, host, datastore, datastore_path}` — CONTRACT §3
  Shape B line extended with `host` + `datastore`.
- PBS webhook body is a base64-encoded handlebars template (`{{ title }}` / `{{ message }}`),
  `{% raw %}`-guarded from Jinja.

Verified: lint gate green (task file is uri-call plumbing; not reachable by the syntax gate
until a bootstrap play imports it — slice 500). NOT verified live: all acceptance items need
a provisioned PBS VM + token (slice 406). API endpoints written against PBS 3.x / PVE 8.x
docs; expect to shake out field-name details on first live run.
