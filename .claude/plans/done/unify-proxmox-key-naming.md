# unify-proxmox-key-naming

<!-- The slug is the item's identity: this file's full name (<slug>.md), the lead of the run branch
(after an optional type segment, e.g. feat/<slug>), and the lead of the squash-commit subject.
Stage owners: korr-design writes Goal/Context/Acceptance criteria; korr-groomer writes
Plan/Decisions/Verification; the build loop (korr-qa driving implementer + reviewer) fills Run log. -->

**Type:** fix

**Depends on:** reconcile-config-example (landed, commit dc1841b)

**Spec:** `.claude/meta/004-proxmox-key-naming/README.md`; `ansible/vars/CONTRACT.md` §5 (`config/proxmox.yml` key table) and §6 (known-conflicts table, row "Proxmox key names")

## Goal

Rename the Proxmox connection keys `proxmox.host` / `proxmox.port` to the canonical
`proxmox.api_host` / `proxmox.api_port` in the two remaining stale spots
(`config.example/proxmox.yml` and the `bootstrap.yml` assert), and update `CONTRACT.md` to record
the conflict as resolved — so a fresh copy of the example file actually satisfies every consumer.

## Context

The contract (CONTRACT.md §6) already decided `api_host` / `api_port` are canonical — they match
the `community.proxmox` module's own parameter names and avoid ambiguity with the Proxmox *node*
host. Slices 001–003 landed the loader and module callers on the canonical names; only two spots
still use the old names, which means a user who copies `config.example/proxmox.yml` to
`config/proxmox.yml` produces `homelabinfra_config.proxmox.host`, and every provisioning task then
fails its `api_host is defined` assert.

**The two stale spots (the whole in-code change):**

- `config.example/proxmox.yml:13-14` — declares `host: ""` and `port: 8006` under the `proxmox:`
  block. The loader (`ansible/tasks/load-user-vars.yml`) injects this block as-is into
  `homelabinfra_config.proxmox.*`, so these key names ARE the runtime key names. Pure rename:
  `host` → `api_host`, `port` → `api_port`; keep the values, inline comments, and surrounding
  layout exactly as they are (the file's landed 002 convention shows optional keys with their
  values inline, unlike `user-vars-example.yml` which comments them out — do not "fix" that here).
- `ansible/playbooks/bootstrap.yml:41` — asserts `homelabinfra_config.proxmox.host is defined`.
  Rename to `api_host`. The sibling asserts (`api_token_id`, `infrastructure.domain`) are already
  correct; do not extend the assert list.

**Already canonical — verify-only, do not touch:**

- `ansible/tasks/proxmox/lxc-create.yml` (assert line 8, module args lines 23-24) and
  `ansible/tasks/proxmox/vm-create.yml` (assert line 7, module args lines 22-23) read
  `homelabinfra_config.proxmox.api_host` / `.api_port`.
- `ansible/vars/user-vars-example.yml` declares `api_host` (required) and comments out `api_port`
  (defaulted).
- `ansible/vars/homelabinfra-defaults.yml:6` defaults `api_port: 8006`.
- `ansible/scripts/with-proxmox-env.sh` parses `api_host` / `api_port` out of a wrapped user-vars
  file to export `PROXMOX_API_*` env vars; `ansible/inventory/proxmox.yml` reads only those env
  vars (never `homelabinfra_config`).

**Contract cleanup (part of this change):** CONTRACT.md §5's `proxmox.api_host` / `proxmox.api_port`
rows carry parentheticals "examples' `host` is a conflict → slice 004" / "examples' `port` →
slice 004", and §6's conflict table has the row "Proxmox key names: examples `proxmox.host`/`.port`
vs defaults/arch `api_host`/`api_port` | `api_host` / `api_port` canonical | **004**". Once the
rename lands these describe a conflict that no longer exists — drop the parentheticals and remove
(or mark resolved) the §6 row so the contract no longer lists it as pending. Historical documents
(`.claude/plans/done/*`, `.claude/meta/*`) mention the old names as history; they are out of scope.

**Verification reality:** the meta README's fourth acceptance bullet (`create-lxc.yml` proceeding
past asserts in `--check` mode with a fresh config copy) needs a live-ish Ansible run the repo's
gates don't perform; the checkable equivalent is that the example file's `proxmox:` block now
declares exactly the keys the asserts and module callers read. The repo gates are
`.claude/build.yml`'s `lint:` and `test:` (ansible-lint and `--syntax-check` over all playbooks,
via WSL wrapper scripts) — both must stay green; neither exercises key names at runtime, so the
grep criteria below carry the real weight.

## Acceptance criteria

- `config.example/proxmox.yml` declares `api_host` and `api_port` under `proxmox:`; no `host:` or
  `port:` key remains anywhere in the file. Values, inline comments, and all other keys unchanged.
- `ansible/playbooks/bootstrap.yml` asserts `homelabinfra_config.proxmox.api_host is defined`; no
  other assert line changed.
- `grep -rn -E "proxmox\.(host|port)\b" ansible/playbooks ansible/tasks ansible/inventory ansible/vars config.example` returns no matches.
- CONTRACT.md §5 no longer flags `api_host`/`api_port` with a "slice 004" conflict parenthetical,
  and the §6 conflict-table row for Proxmox key names is removed (not marked resolved in place —
  its literal `proxmox.host` token would fail the grep criterion above).
- `lint:` and `test:` gates from `.claude/build.yml` pass (evidence pasted in the Run log).

## Plan

Three files change: two key renames in user-facing surfaces and a contract cleanup. No executable
task/role/loader logic is touched (the module callers already read the canonical names — verified
this session at `ansible/tasks/proxmox/lxc-create.yml:8,23-24` and `vm-create.yml:7,22-23`). Apply
the edits below verbatim.

### File 1 — `config.example/proxmox.yml` (rename `host`→`api_host`, `port`→`api_port`; values, comment text, and comment-column alignment preserved)

Change lines 13-14 only. The `api_host` line keeps the same inline comment at the same column, so
its padding shrinks by 4 spaces (the key grew from `host` to `api_host`). Every other line in the
file — the header comment block, `networks:`, `ansible:` — stays byte-for-byte.

Replace:
```yaml
  host: ""              # Proxmox IP or hostname (required)
  port: 8006
```
with:
```yaml
  api_host: ""          # Proxmox IP or hostname (required)
  api_port: 8006
```

(The `#` stays at column 25: `  api_host: ""` is 14 chars, then 10 spaces, then `#`. `api_port`
has no inline comment. Do not touch `networks.default.max_hosts` — it is a different key that merely
contains the substring "host".)

### File 2 — `ansible/playbooks/bootstrap.yml` (rename the assert key; no other assert line changed)

Change line 41 only. The sibling asserts (`api_token_id`, `infrastructure.domain`) are already
correct — do not extend or reorder the list.

Replace:
```yaml
          - homelabinfra_config.proxmox.host is defined
```
with:
```yaml
          - homelabinfra_config.proxmox.api_host is defined
```

### File 3 — `ansible/vars/CONTRACT.md` (drop the resolved-conflict annotations in §5 and §6)

**Edit 3a — §5 `config/proxmox.yml` table, remove the "slice 004" parentheticals from the
`api_host`/`api_port` rows.**

Replace:
```markdown
| `proxmox.api_host` | required | canonical name; examples' `host` is a conflict → slice 004 |
| `proxmox.api_port` | optional | default `8006` (canonical name; examples' `port` → slice 004) |
```
with:
```markdown
| `proxmox.api_host` | required | canonical name |
| `proxmox.api_port` | optional | default `8006` (canonical name) |
```

**Edit 3b — §6 known-conflicts table, remove the "Proxmox key names" row entirely.**

Delete this line (the whole table row):
```markdown
| Proxmox key names: examples `proxmox.host`/`.port` vs defaults/arch `api_host`/`api_port` | `api_host` / `api_port` canonical | **004** |
```
The row sits between the table header/separator (lines 114-115) and the `config.example/*.yml`
unwrapped-keys row (line 117); removing it leaves both intact and the markdown table well-formed.
No other row or line in §6 changes.

### Test-first note

This change touches no executable code — there is no unit under test to write. The "test" is the
two static gates plus the acceptance grep and the diff inspection in `## Verification`. The
implementer applies the three files' edits exactly, runs both gates and the grep, and records the
evidence in the Run log.

## Decisions

- **§6 conflict row — remove vs mark-resolved-in-place → remove.** The acceptance grep
  (`proxmox\.(host|port)\b` over `ansible/vars` among others) matches the literal `proxmox.host`
  token inside the §6 row, so leaving the row in place while "marking it resolved" would still fail
  the grep unless the text were reworded to strip the `proxmox.` prefix. Removal is the unambiguous
  path that satisfies both the grep and the acceptance ("removed or marked resolved"), and the
  dossier lists removal first. The resolved history is not lost: §5 now states `api_host`/`api_port`
  as canonical outright, and this plan (slice 004) landing is itself the record in `done/`. The
  neighbouring resolved `001 + 002` row (line 117) is left as-is — it carries no grep-matching token
  and is out of this item's scope.
- **`config.example/proxmox.yml` inline-comment column — preserve alignment vs let it drift →
  preserve.** The dossier asks to keep "the inline comments and surrounding layout exactly as they
  are." Renaming `host`→`api_host` lengthens the key by 4 chars, so to keep the `#` comment at its
  existing column (25) the padding on that line is reduced by 4 spaces. This keeps the file's
  value/comment table visually aligned (matching the `node`/`api_token_id`/`api_token_secret` rows)
  rather than pushing one comment out of line. Whitespace-only, ≥2 spaces before `#`, so yamllint is
  unaffected. `api_port` has no inline comment, so it is a straight rename.
- **`bootstrap.yml` assert list — rename only vs also add missing asserts → rename only.** The
  dossier is explicit that the sibling asserts (`api_token_id`, `infrastructure.domain`) are already
  correct and the list must not be extended. This item is the two-spot rename, nothing more.
- **Canonical spots and historical documents → verify-only, do not touch.** Confirmed this session
  that `ansible/tasks/proxmox/lxc-create.yml`, `vm-create.yml`, `ansible/vars/user-vars-example.yml`,
  `ansible/vars/homelabinfra-defaults.yml`, `ansible/scripts/with-proxmox-env.sh`, and
  `ansible/inventory/proxmox.yml` all already use `api_host`/`api_port` (or read only the
  `PROXMOX_API_*` env vars). `.claude/plans/done/*` and `.claude/meta/*` mention the old names as
  history and are out of scope. No edit needed or made to any of these.

## Verification

### Static gates (`.claude/build.yml` — the only real gates)

Run both, capturing the real exit code inside WSL (a Bash-tool "exit 1" on these has twice been a
WSL relay artifact — see `.claude/plans/done/reconcile-config-example.md` Run log):

- `lint`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh; echo RC=$?'`
- `test`: `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh; echo RC=$?'`

**Expected:** `lint` → `RC=0` (ansible-lint over `playbooks/`/`roles/`/`tasks/`/`vars/`; it parses
the edited `bootstrap.yml` and `CONTRACT.md` is not YAML so is untouched — no new diagnostics vs
base). `test` → `RC=0` with only the known pre-existing `[ERROR]` diagnostics identical to base
(docker role not found in `playbooks/docker/create-docker-host.yml`; `'instance' is undefined` in
`maintenance/restart-app.yml` and `maintenance/tail-applog.yml`; empty `stacks/rollback-container.yml`)
— a diagnostic that is new or gone is a regression to investigate. Neither gate parses
`config.example/*.yml` and neither exercises key names at runtime, so the grep below carries the
real weight.

### Acceptance grep (the load-bearing check)

Run the exact criterion from the dossier:

```
grep -rn -E "proxmox\.(host|port)\b" ansible/playbooks ansible/tasks ansible/inventory ansible/vars config.example
```

**Expected:** no matches (exit 1, no output). Before the change this returns exactly two lines
(`ansible/playbooks/bootstrap.yml:41` and `ansible/vars/CONTRACT.md:116`); both are eliminated by
Files 2 and 3b.

### Diff inspection (proves what the gates cannot)

1. **`config.example/proxmox.yml`** — the `proxmox:` block declares `api_host` and `api_port`; no
   `host:` or `port:` key remains (the `max_hosts:` key under `networks.default` is unrelated and
   unchanged). The `api_host` value (`""`), its inline comment text, and every other line are
   unchanged; only the two key names and the padding on the `api_host` comment moved.
2. **`ansible/playbooks/bootstrap.yml`** — line 41 asserts
   `homelabinfra_config.proxmox.api_host is defined`; the two sibling assert lines and the
   `fail_msg` are byte-for-byte unchanged.
3. **`ansible/vars/CONTRACT.md`** — §5's `api_host`/`api_port` rows no longer carry a "slice 004"
   parenthetical; §6 no longer contains a "Proxmox key names" row, and the §6 table is still
   well-formed (header, separator, and remaining five rows intact).
4. **Diff scope** — `git diff --name-only` shows exactly three changed files:
   `config.example/proxmox.yml`, `ansible/playbooks/bootstrap.yml`, `ansible/vars/CONTRACT.md`
   (plus this plan file). No task, role, loader, `homelabinfra-defaults.yml`, or
   `user-vars-example.yml` change appears.

### korr-qa senior pass confirms before commit

Both gates green with pre-existing diagnostics identical to base; the acceptance grep returns no
matches; the four inspection points above hold on the diff; diff scope is exactly the three target
files.

## Run log

<Empty until the first round; korr-qa drives it.>

### [implementer] round 1

Applied the three file edits verbatim per the plan's `## Plan` section.

**Files touched:**
- `config.example/proxmox.yml` — lines 13-14: `host` → `api_host` (kept value `""`, inline comment,
  and comment column — padding shrank by 4 spaces to keep `#` at column 25), `port` → `api_port`
  (kept value `8006`, no inline comment). Rest of file byte-for-byte unchanged.
- `ansible/playbooks/bootstrap.yml` — line 41 assert key `homelabinfra_config.proxmox.host` →
  `homelabinfra_config.proxmox.api_host`. Sibling asserts (`api_token_id`,
  `infrastructure.domain`) and `fail_msg` untouched.
- `ansible/vars/CONTRACT.md` — Edit 3a: dropped the "slice 004" parentheticals from the §5
  `proxmox.api_host` / `proxmox.api_port` table rows. Edit 3b: deleted the §6 "Proxmox key names"
  conflict-table row entirely (table header/separator and remaining five rows intact).

Verified before/after: only these three files changed (plus the run lock and a pre-existing,
unrelated `.claude/settings.local.json` modification / `.claude/agents/fable-groomer.md` untracked
file that predate this round — not touched by this work).

**Gate evidence:**

`lint` — `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh; echo RC=$?'`
```
RC=0
...
Passed: 0 failure(s), 0 warning(s) in 55 files processed of 64 encountered. Profile 'min' was required, but 'production' profile passed.
```

`test` — `wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh; echo RC=$?'`
```
RC=0
...
[ERROR]: the role 'docker' was not found in .../ansible/playbooks/docker/roles ... (playbooks/docker/create-docker-host.yml:114) — pre-existing, listed in Verification as expected
[ERROR]: Error processing keyword 'hosts': 'instance' is undefined (playbooks/maintenance/restart-app.yml:11) — pre-existing, expected
[ERROR]: Error processing keyword 'hosts': 'instance' is undefined (playbooks/maintenance/tail-applog.yml:12) — pre-existing, expected
[ERROR]: Empty playbook, nothing to do: playbooks/stacks/rollback-container.yml — pre-existing, expected
```
All four `[ERROR]` diagnostics match the plan's documented pre-existing/base set exactly — none new,
none gone.

**Acceptance grep** — ran the equivalent of `grep -rn -E "proxmox\.(host|port)\b" ansible/playbooks
ansible/tasks ansible/inventory ansible/vars config.example` (via the Grep tool, one directory at a
time, same pattern): **no matches in any of the five directories** — matches the plan's expected
outcome (previously two matches: `bootstrap.yml:41` and `CONTRACT.md:116`, both eliminated).

**Diff scope** — `git diff --name-only` shows exactly the three target files changed:
`config.example/proxmox.yml`, `ansible/playbooks/bootstrap.yml`, `ansible/vars/CONTRACT.md` (plus
the pre-existing `.claude/settings.local.json` change present before this round started, unrelated
to this plan). No task/role/loader file touched.

No decisions needed — the plan's `## Decisions` section covered every judgment call encountered.
Working tree left uncommitted on `fix/unify-proxmox-key-naming` for review.

### [reviewer] verdict: PASS

Verified independently: `git diff --stat` shows exactly the three target files plus the plan's own run-log growth changed (`.claude/settings.local.json` is pre-existing/unrelated as the implementer noted, `.claude/agents/fable-groomer.md` is untracked and untouched). `config.example/proxmox.yml` lines 13-14 are `api_host`/`api_port` with values, inline comment text, and comment-column alignment preserved (confirmed by column-index check: `#` sits at column 25 on both the old `host` line and the new `api_host` line, matching the `node` row); no other line in the file changed. `ansible/playbooks/bootstrap.yml` line 41 now asserts `homelabinfra_config.proxmox.api_host is defined`; the two sibling assert lines and `fail_msg` are byte-for-byte unchanged. `ansible/vars/CONTRACT.md` §5's two rows dropped the "slice 004" parentheticals exactly as specified, and §6's "Proxmox key names" row is deleted entirely with the table (header, separator, 5 remaining rows) still well-formed. Ran the acceptance grep myself: `grep -rn -E "proxmox\.(host|port)\b" ansible/playbooks ansible/tasks ansible/inventory ansible/vars config.example` — no matches (exit 1), matching the plan's expected outcome. Gate evidence pasted is green (`lint` RC=0 clean; `test` RC=0 with the four pre-existing `[ERROR]` diagnostics named in Verification, none new/gone) — did not re-run, evidence is internally consistent and matches the documented base set. No reflex findings (no secrets, no injection, no swallowed errors; nothing touches namespace merges, arithmetic typing, or `hosts: proxmox_nodes` plays). No trivia fixes needed — diff matches the plan verbatim.

[qa] verdict: PASS
 - Senior pass: read the full three-file diff — edits match the plan's `## Plan` section verbatim (rename-only in the example and the bootstrap assert; §5 parentheticals dropped and §6 row removed in CONTRACT.md, table well-formed). Independently re-ran the acceptance grep over `ansible/` and `config.example/` — zero `proxmox.host`/`proxmox.port` matches; the only remaining `host:` key in config.example is the unrelated, documented `dns.host` in infrastructure.yml. Gate evidence (lint RC=0, test RC=0 with the four documented pre-existing diagnostics) is internally consistent with the base set named in `## Verification`; no spot-run needed for a no-executable-code change whose real weight is the grep. No agent surfaced a decision. Clear to commit.
