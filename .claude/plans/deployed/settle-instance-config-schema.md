# settle-instance-config-schema

**Type:** fix

**Depends on:** —

**Spec:** `.claude/meta/005-instance-config-schema/README.md` (the slice this promotes);
`.claude/specs/config-layering.md`; `ansible/vars/CONTRACT.md` §6 + "App-level layering note"

## Goal

Make the two instance-config examples (`config.example/apps/radarr.example.yml`,
`config.example/apps/_template.example.yml`) teach the one schema the app-playbook merge actually
consumes, and settle that schema in `ansible/vars/CONTRACT.md` (which names slice 005 as the
owner). Examples-and-contract-doc change only — no playbook, task, or role logic is touched.

## Context

### The merge is the contract — verified this session, do not change it

`ansible/playbooks/apps/_template.yml:51-53` does:

```yaml
app_config: "{{ _app_defaults.APP_NAME_defaults | combine(_instance_config | default({}), recursive=True) }}"
```

where `_instance_config` is the **whole** `config/apps/{{ instance }}.yml` file
(`_template.yml:40-44`, loaded by filename from `-e instance=<name>` — filename **is** the
instance name). Therefore the instance file's top-level keys must mirror the top-level keys of
the `<app>_defaults` dict in `vars/app-defaults/<app>.yml`. Per
`vars/app-defaults/_template.yml` those are exactly:

- `proxmox:` (native LXC apps) **or** `stack:` (Docker apps — a scalar, e.g. `media_stack`)
- `app:` — port, data_path, config_path, plus app-specific keys
- `update:` — optional, native-LXC GitHub-release apps only (`github_repo`, `binary_path`)
- `routing:` — `proxy` (internal|external), `auth` (bool)

What code actually reads out of `app_config` (grep evidence, this session):
`app_config.stack` (`_template.yml:63`), `app_config.proxmox.network` (`:70`),
`app_config.app.port` (`_template.yml:128` wiring contract; both template roles' health checks
and compose/config templates), `app_config.app.data_path` / `.config_path` (both template
roles), `app_config.update.github_repo` / `.binary_path` (`roles/_template-native`),
`app_config.routing.auth` (`_template.yml:143`). **Nothing** reads `app_config.radarr.*`,
`app_config.instance_name`, or `app_config.APP_NAME.*`.

### Defects, file by file

**`config.example/apps/radarr.example.yml`** — three wrong keys, one of them destructive:

1. `app: radarr` (line 7) — not merely an orphan. The defaults' `app:` is a **mapping**;
   `combine(recursive=True)` replaces a mapping with a scalar when the override side is a
   scalar, so this line **clobbers the entire `app:` dict** — `app_config.app.port` is then
   undefined and the wire play (`_template.yml:128`) hard-fails. Worse than the meta README's
   "orphan keys" framing; state this in the rewrite's history if useful, but the fix is simply
   to delete the line.
2. `instance_name: radarr` (line 11) — orphan. Instance identity comes from
   `-e instance=<name>` = the config filename; no code reads `app_config.instance_name`. The
   *guidance* in its comment (unique names for multiple instances: radarr-4k, radarr-kids) is
   good and should survive as a comment about choosing the **filename**.
3. Nested `radarr:` block (lines 24-27) — orphan; lands at `app_config.radarr.*` which nothing
   reads. Its content (`root_folder`, `quality_profile`, `port`) belongs under `app:` — per the
   meta README, a flat structure under `app:` matching what the role will read. `port` under
   `app:` is the real override point (`app.port` in defaults).

The commented `proxmox:` cores/memory block (lines 15-17) is wrong for a Docker app in spirit —
Radarr is a Docker-on-LXC app (per `playbooks/apps/README.md` step 1 table) whose resources
belong to the shared **stack host**, not the app; the per-app knob is `stack:`. The commented
`stack:` and `routing:` blocks are already correct in shape.

**`config.example/apps/_template.example.yml`** — authoritative in doctrine (filename =
instance name, override-only keys, all optional keys commented out) but carries the same
disease in miniature: its "App-specific config" block (lines 20-21) teaches

```yaml
# APP_NAME:
#   port: 8080
```

— a nested app-name key that would land as orphan `app_config.APP_NAME.*`. The consumed path is
`app.port`. This block must become an `app:` block. Everything else in the file matches the
defaults-template shape and the config-layering rules; keep it.

**`ansible/vars/CONTRACT.md`** — the contract *names* the instance-schema conflict and defers it
to slice 005 in three places: §2 table row for `config/apps/<instance>.yml` ("schema
contradictory today → slice 005", line 27), §5 closing paragraph ("owned by slice 005", line
109), §6 conflict-table row ("named, not resolved here | **005**", line 119). This plan **is**
slice 005 landing, so the contract's "App-level layering note" (lines 122-127) gains the settled
schema — filename = instance name; top-level keys mirror the `<app>_defaults` dict
(`proxmox:`|`stack:`, `app:`, `update:`, `routing:`); whole file merges over defaults via
`combine(recursive=True)` — and the three deferral mentions flip from "unresolved / owned by
005" to pointing at the now-stated schema. Keep the note's existing warning that this merge is
separate from the four-layer `homelabinfra_config` merge.

### Radarr is aspirational — keep the example anyway

No `vars/app-defaults/radarr.yml`, no `roles/radarr/`, no `playbooks/apps/radarr.yml` exist
(only `vaultwarden.yml` defaults and the `_template-*` roles do; verified this session). The
radarr example is the worked per-app demo ahead of any radarr implementation, and the meta
README says keep it as "a useful demo of overriding a couple of values." Its header comment
references `vars/app-defaults/radarr.yml`; that pointer describes the file a future radarr
contribution adds per `playbooks/apps/README.md` step 2 — groomer decides the comment wording so
it doesn't read as a dangling reference.

### Rules that bind the rewrite (`.claude/specs/config-layering.md`)

- Optional keys appear **commented out, never as empty values** — an uncommented empty-string or
  `0` in an example blanks the git-managed default under `combine`.
- Uncommented keys must be real demonstration values (the radarr example should keep a couple,
  per the meta), and every uncommented key must land at a path the code reads.

### Out of scope — owned elsewhere, do not touch

- **`ansible/playbooks/apps/_template.yml`** — the merge step is the contract, keep as is (meta
  005 says so explicitly); the file is also under active edit by
  `.claude/plans/active/fix-app-template-wiring-facts.md`. This plan must not touch it.
- **`playbooks/apps/README.md`** — already teaches the right doctrine (filename = instance,
  user-facing knobs only); no schema statement in it contradicts the fix. No edit.
- **`vars/app-defaults/_template.yml`** — already correct; it is the shape source, not a defect.
- Creating radarr's defaults/role/playbook — future app contribution, not this slice.

### Gate reality (for Verification)

The only gates are `lint` and `test` in `.claude/build.yml` (WSL wrappers over
`.claude/gate/lint.sh` / `test.sh`). **Neither parses `config.example/*.yml`** (outside lint
targets `playbooks/`/`roles/`/`tasks/`/`vars/`) and CONTRACT.md is markdown — so the gates prove
only "no regression elsewhere"; the schema acceptance is proven by inspection trace against the
merge and the grep list above. Known pre-existing `test`-gate `[ERROR]` diagnostics (docker role
resolution, `instance` undefined in restart/tail playbooks, empty rollback playbook) are
accepted only if identical on base — see `.claude/plans/done/reconcile-config-example.md` Run
log. Capture the real exit code with `; echo RC=$?` inside the WSL call (Bash-tool "exit 1" on
these gates has repeatedly been a WSL relay artifact).

## Acceptance criteria

- `config.example/apps/radarr.example.yml` has no `app: <scalar>` line, no `instance_name:` key,
  and no nested `radarr:` block; every top-level key (commented or not) is one of
  `stack`/`app`/`routing`/`update`/`proxmox`, and every **uncommented** key traces to a path the
  code reads (`app_config.app.*`, `app_config.stack`, `app_config.routing.*`) — verifiable from
  the diff against the grep list in Context.
- `config.example/apps/_template.example.yml`'s app-specific block teaches `app:` (not
  `APP_NAME:` as a top-level key); the filename-=-instance-name doctrine and
  override-only-commented-keys shape are retained.
- Both example files teach the same shape as `vars/app-defaults/_template.yml`'s top-level keys;
  a user copying either into `config/apps/<instance>.yml` produces a merge with no orphan and no
  clobbered key.
- No config-layering violation introduced: optional keys commented out; no uncommented
  empty-string/`0` value that blanks a git-managed default.
- `ansible/vars/CONTRACT.md` states the settled instance-file schema in the "App-level layering
  note" (filename = instance name; top-level keys mirror `<app>_defaults`; whole-file
  `combine(recursive=True)` over defaults), and no longer describes the schema as
  contradictory/unresolved in §2 (line 27), §5 (line 109), or §6 (line 119).
- Diff scope: exactly `config.example/apps/radarr.example.yml`,
  `config.example/apps/_template.example.yml`, `ansible/vars/CONTRACT.md`. No playbook, task,
  role, or vars YAML changes.
- The `lint` and `test` gates in `.claude/build.yml` pass with results identical to base
  (pre-existing diagnostics enumerated in Context, and only those).

## Plan

Three files, doc/example only. No test-first step applies — there is no gate that parses
`config.example/*.yml` or CONTRACT.md (see Verification); correctness is proven by inspection
trace plus a no-regression gate run. Apply the three edits below verbatim.

### File 1 — `config.example/apps/radarr.example.yml` (full rewrite)

Replace the entire file with the block below. This deletes the clobbering `app: radarr` scalar,
the orphan `instance_name:` key, the orphan nested `radarr:` block, and the inapplicable commented
`proxmox:` block (Radarr is a Docker app — see Decision C). It keeps two uncommented demo overrides
(`stack`, `app.root_folder`), both tracing to consumed `app_config` paths (Decision B), and the
already-correct commented `routing:` block.

```yaml
---
# Radarr instance config.
# Copy to config/apps/radarr.yml (or radarr-4k.yml, radarr-kids.yml, etc.).
# The filename you choose IS the instance name: it becomes the hostname,
# Caddy subdomain (radarr.yourdomain.com), and Authentik app name.
# For multiple instances, use unique filenames: radarr-4k.yml, radarr-hd.yml, radarr-kids.yml.
#
# Only set values that differ from the defaults. Radarr's defaults live in
# vars/app-defaults/radarr.yml, added by the radarr app contribution
# (see playbooks/apps/README.md for the contributor guide).
# This file persists after removal — it is your restore point.

# ── Stack assignment (Docker app) ──────────────────────────────────────────────
# Radarr runs on a shared Docker stack host, so its CPU/RAM belong to that host,
# not to this file. The per-app knob is which stack host to place it on.
stack: media_stack     # which Docker stack host this app runs on

# ── App config ─────────────────────────────────────────────────────────────────
app:
  root_folder: /media/movies   # path inside the container to your movie library
  # port: 7878                 # override the default port only when running
  #                            # multiple Radarr instances on the same stack host

# ── Routing ────────────────────────────────────────────────────────────────────
# routing:
#   proxy: internal            # internal | external (which reverse proxy handles this app)
#   auth: true                 # true = wire Authentik SSO, false = skip (default from app-defaults)
```

### File 2 — `config.example/apps/_template.example.yml` (one-line key fix)

Change only the app-specific block's key from the orphan `APP_NAME:` to the consumed `app:`.
Everything else in the file (filename-=-instance doctrine, commented `proxmox:` and `stack:`
blocks, commented `routing:` block) is already correct — leave it untouched.

Replace this block (lines 19-21):

```yaml
# ── App-specific config ────────────────────────────────────────────────────────
# APP_NAME:
#   port: 8080         # only needed if running multiple instances on the same stack host
```

with:

```yaml
# ── App-specific config ────────────────────────────────────────────────────────
# app:
#   port: 8080         # only needed if running multiple instances on the same stack host
```

### File 3 — `ansible/vars/CONTRACT.md` (four edits — three deferral flips + schema statement)

**Edit 3a — §2 load-map row (line 27).** Replace:

```
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | schema contradictory today → slice 005 |
```

with:

```
| `config/apps/<instance>.yml` | none | `app_config` (per-play app merge) | whole file merges over `<app>_defaults` via `combine(recursive=True)` — see app-layering note |
```

**Edit 3b — §5 closing paragraph (lines 107-110).** Replace:

```
The required/optional split for `config/.generated/facts.yml` follows the canonical shape in
Section 3 but its authoritative required-key list is owned by **slice 200** (it defines what
bootstrap writes); the `config/apps/<instance>.yml` schema is owned by **slice 005**. Contract names
them here, does not resolve them.
```

with:

```
The required/optional split for `config/.generated/facts.yml` follows the canonical shape in
Section 3 but its authoritative required-key list is owned by **slice 200** (it defines what
bootstrap writes); the Contract names it here, does not resolve it. The `config/apps/<instance>.yml`
schema is settled in the App-level layering note below.
```

**Edit 3c — §6 conflict-table row (line 119).** Replace:

```
| `config/apps/<instance>.yml` schema contradictory across repo | named, not resolved here | **005** |
```

with:

```
| `config/apps/<instance>.yml` schema across the repo | settled: filename = instance name; top-level keys mirror `<app>_defaults`; whole file merges via `combine(recursive=True)` — see App-level layering note | **005 (settled)** |
```

**Edit 3d — App-level layering note (lines 122-127).** The note currently reads:

```
The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a
**separate** per-play merge done in the app template, **not** part of `homelabinfra_config`. It is
described here for completeness but governed by its own precedence; do not conflate it with the
four-layer `homelabinfra_config` merge in Section 4.
```

Append a new paragraph immediately after it (keep the existing paragraph unchanged), so the note
becomes:

```
The per-app merge (`vars/app-defaults/<app>.yml` → `config/apps/<instance>.yml` → `app_config`) is a
**separate** per-play merge done in the app template, **not** part of `homelabinfra_config`. It is
described here for completeness but governed by its own precedence; do not conflate it with the
four-layer `homelabinfra_config` merge in Section 4.

**Instance-file schema (settled by slice 005).** `config/apps/<instance>.yml` is loaded whole by
filename — the filename *is* the instance name (`-e instance=<name>`) and becomes the hostname,
Caddy subdomain, and Authentik app name. Its top-level keys mirror the `<app>_defaults` dict in
`vars/app-defaults/<app>.yml`: `proxmox:` (native LXC) **or** `stack:` (Docker apps — a scalar such
as `media_stack`), `app:` (port, data_path, config_path, plus app-specific keys), optional `update:`
(`github_repo`, `binary_path` — native GitHub-release apps only), and `routing:` (`proxy`, `auth`).
The whole file merges over `<app>_defaults` via `combine(recursive=True)`, later layer wins per key.
Because the merge is recursive, an override must match the default's shape: replacing a mapping (e.g.
`app:`) with a scalar clobbers the entire subtree, so instance files never restate a mapping key as a
bare scalar.
```

### Do NOT touch

`ansible/playbooks/apps/_template.yml` (the merge is the contract; also under active edit by
`fix-app-template-wiring-facts.md`), `vars/app-defaults/_template.yml`, `playbooks/apps/README.md`,
and any playbook/task/role/vars YAML. Diff scope is exactly the three files above.

## Decisions

- **A — radarr header comment wording (avoid dangling `vars/app-defaults/radarr.yml` reference).**
  Kept the pointer but framed the file as *not-yet-existing*: "Radarr's defaults live in
  vars/app-defaults/radarr.yml, added by the radarr app contribution (see playbooks/apps/README.md
  for the contributor guide)." Why: the example is aspirational (no radarr defaults/role/playbook
  exist this session), and README.md step 2 is exactly where a future radarr contribution adds that
  file. Phrasing it as "added by the contribution" reads as forward-looking, not broken.

- **B — which uncommented values stay as the working demo.** Kept exactly two, forming a coherent
  story ("Radarr on the media_stack, pointed at your movie library"): `stack: media_stack` →
  consumed at `app_config.stack` (`_template.yml:63`); and an `app:` block with
  `root_folder: /media/movies` → lands at `app_config.app.root_folder`, matching the
  `app_config.app.*` namespace the acceptance criteria and defaults template ("plus app-specific
  keys") sanction. Why these two: both trace to consumed paths, "a couple" per the meta README, and
  `root_folder` is the one override a real Radarr user actually cares about. `app.port` stays
  commented — it is a conditional ("only when running multiple instances") knob, wrong as the
  headline demo. `routing:` stays fully commented — defaults already cover it, and uncommenting it
  would only restate defaults.

- **C — drop the commented `proxmox:` cores/memory block from the radarr example.** Dropped. Why:
  Radarr is a Docker-on-LXC app; per `vars/app-defaults/_template.yml` a Docker app's defaults
  replace the whole `proxmox:` block with a single `stack:` scalar, so a Docker instance file has no
  `proxmox:` key to override — a `proxmox:` block there would land as orphan `app_config.proxmox.*`
  and teach a knob that does not apply. The per-app resource knob for a Docker app is *which stack
  host* (`stack:`), which the example keeps uncommented. The app-agnostic `_template.example.yml`
  still keeps its commented `proxmox:` block (native-LXC path) alongside the commented `stack:`
  block — the template teaches both hosting shapes; the known-Docker radarr example teaches only its
  own.

- **D — CONTRACT.md deferral wording.** §2 row (line 27): "schema contradictory today → slice 005" →
  "whole file merges over `<app>_defaults` via `combine(recursive=True)` — see app-layering note".
  §5 (lines 107-110): removed "the `config/apps/<instance>.yml` schema is owned by slice 005" and
  pointed to the now-settled App-level layering note, while leaving the facts.yml / slice-200
  ownership sentence intact. §6 row (line 119): "named, not resolved here | **005**" →
  "settled: filename = instance name; top-level keys mirror `<app>_defaults`; whole file merges via
  `combine(recursive=True)` — see App-level layering note | **005 (settled)**" (row kept, not
  deleted, so the conflict's history stays auditable). App-level layering note: appended a new
  "Instance-file schema (settled by slice 005)" paragraph stating filename = instance name,
  top-level keys mirror `<app>_defaults` (`proxmox:`|`stack:`, `app:`, optional `update:`,
  `routing:`), and whole-file `combine(recursive=True)` precedence — including the
  mapping-clobbered-by-scalar rule that the radarr `app: radarr` defect violated. The pre-existing
  paragraph (merge is separate from the four-layer `homelabinfra_config` merge) is preserved
  verbatim. Why keep the note's existing warning: the dossier requires it, and it prevents readers
  conflating the per-app merge with Section 4.

## Verification

### Gates (no-regression only)

Neither gate parses `config.example/*.yml` (lint targets are `playbooks/`/`roles/`/`tasks/`/`vars/`)
and CONTRACT.md is markdown, so the gates prove *only* that nothing else regressed. Run both from
`.claude/build.yml`, capturing the real exit code inside the WSL call (Bash-tool "exit 1" on these
gates has repeatedly been a WSL-relay artifact — trust `RC=`):

```
wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/lint.sh ; echo RC=$?'
wsl bash -lc 'cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra && bash .claude/gate/test.sh ; echo RC=$?'
```

- **Expectation:** results identical to base. The `test` gate's known pre-existing `[ERROR]`
  diagnostics (docker-role resolution; `instance` undefined in restart/tail playbooks; empty
  rollback playbook) are accepted **only if** they match base one-for-one — cross-check against
  `.claude/plans/done/reconcile-config-example.md` Run log. Any *new* diagnostic, or any diagnostic
  naming one of the three touched files, fails the change.

### Inspection trace (this is what proves the schema landed)

- **radarr example — every uncommented key → consumed path.** `stack: media_stack` →
  `app_config.stack` (read at `_template.yml:63`). `app.root_folder` → `app_config.app.root_folder`
  (the `app_config.app.*` namespace the merge exposes and a radarr role reads). No other uncommented
  top-level key exists. Confirm the file has **no** `app: <scalar>` line, **no** `instance_name:`
  key, **no** nested `radarr:` block, and **no** `proxmox:` block.
- **radarr example — every top-level key (commented or not) is one of**
  `stack`/`app`/`routing`/`update`/`proxmox`. Present: `stack`, `app`, `routing` (commented). Absent
  by design: `update`, `proxmox`.
- **_template example — app-specific block teaches `app:`,** not `APP_NAME:` as a top-level key; the
  filename-=-instance doctrine and all-optional-keys-commented shape are retained; `proxmox:` and
  `stack:` blocks both still present and commented.
- **Both examples mirror `vars/app-defaults/_template.yml` top-level keys** (`proxmox:`|`stack:`,
  `app:`, `update:`, `routing:`), so a user copying either into `config/apps/<instance>.yml` merges
  with no orphan key and no clobbered mapping.
- **Config-layering compliance:** no uncommented empty-string or `0` value that would blank a
  git-managed default under `combine`; all optional keys are commented out.
- **CONTRACT.md no longer describes the schema as unresolved:** §2 line-27 row, §5 closing
  paragraph, and §6 line-119 row no longer say "contradictory / owned by 005 / not resolved here";
  the App-level layering note now states the settled schema (filename = instance name; top-level
  keys mirror `<app>_defaults`; whole-file `combine(recursive=True)`), and its existing
  "separate from the four-layer merge" warning is intact.

### Diff scope

`git status --short` shows exactly three modified files:
`config.example/apps/radarr.example.yml`, `config.example/apps/_template.example.yml`,
`ansible/vars/CONTRACT.md`. No playbook, task, role, or vars YAML in the diff — in particular
`ansible/playbooks/apps/_template.yml` is untouched.

### korr-qa senior pass confirms

The two examples now teach only what the app-playbook merge consumes, the CONTRACT.md schema
statement matches the merge in `_template.yml:51-53`, and the change introduced no schema claim that
contradicts the running code.

## Run log

<!-- example shape (delete when the first real round lands):
### round 1
[implementer] what changed, files touched, notes for the reviewer; may flag "(decision needed: …)"
 - test: `pytest -q` → exit 0 (14 passed)
 - lint: `ruff check .` → exit 0
[reviewer] verdict: CHANGES
 - finding: what failed, where, why — actionable without re-reviewing

### round 2
[implementer] what changed in response
 - test: `pytest -q` → exit 0 (15 passed)
[reviewer] verdict: PASS
 - fixed in place: typo in docs/usage.md (no decision involved)
[qa] verdict: PASS
 - senior notes; any agent-surfaced notification lifted here
-->

### round 1

[implementer] Applied the three plan edits verbatim.

- **File 1** — `config.example/apps/radarr.example.yml`: full rewrite per the plan's block. Removed
  the clobbering `app: radarr` scalar, the orphan `instance_name:` key, the orphan nested `radarr:`
  block, and the commented `proxmox:` block (Decision C). Kept two uncommented demo overrides
  (`stack: media_stack`, `app.root_folder`) and the commented `routing:` block.
- **File 2** — `config.example/apps/_template.example.yml`: changed only the app-specific block's
  key from `# APP_NAME:` to `# app:`; everything else untouched.
- **File 3** — `ansible/vars/CONTRACT.md`: applied edits 3a (§2 row), 3b (§5 closing paragraph), 3c
  (§6 row), 3d (appended "Instance-file schema (settled by slice 005)" paragraph to the App-level
  layering note, existing paragraph preserved verbatim). All four before/after texts matched the
  live file exactly before editing.

Diff scope verified: `git status --short` shows exactly `ansible/vars/CONTRACT.md`,
`config.example/apps/_template.example.yml`, `config.example/apps/radarr.example.yml` modified (plus
a pre-existing unrelated `.claude/settings.local.json` modification and untracked
`.claude/agents/fable-groomer.md` / `.claude/autobuild/` that predate this round — not part of this
plan's content). `ansible/playbooks/apps/_template.yml` and all other out-of-scope files: untouched.

**Environment note (not a plan defect):** `.claude/build.yml`'s gate commands hardcode
`cd /mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra`, which does not exist on this machine — the
actual WSL mount for this clone is `/mnt/c/Users/korr/source/repos/homelab-infra`. Ran both gates
via the corrected path (same committed scripts, `.claude/gate/lint.sh` / `test.sh`, unchanged) and
recorded a base-vs-branch comparison to satisfy the plan's "identical to base" requirement. Flagging
for design/build.yml maintenance — not fixed here since `build.yml` is outside this plan's diff
scope.

Gate evidence:

- lint (branch): `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash
  .claude/gate/lint.sh ; echo RC=$?'` → `RC=0`. Tail: `Passed: 0 failure(s), 0 warning(s) on 64
  files. Profile 'min' was required, but 'production' profile passed.`
- test (branch): `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash
  .claude/gate/test.sh ; echo RC=$?'` → `RC=0`. Exactly three `[ERROR]` diagnostics: `'instance' is
  undefined` in `playbooks/maintenance/restart-app.yml` (line 10) and
  `playbooks/maintenance/tail-applog.yml` (line 11), and `Empty playbook, nothing to do:
  .../playbooks/stacks/rollback-container.yml`. No diagnostic named `docker` role missing this run —
  the `docker` role now exists at `ansible/roles/docker/` (resolved by prior work, not this plan).
- test (base): stashed the three plan-scoped edits (`git stash push -m ... -- <3 files>`), re-ran the
  identical test-gate command on the unmodified tree → `RC=0`, byte-identical `[ERROR]` output (same
  three diagnostics, same lines/files). `git stash pop` restored the plan edits cleanly (verified via
  `git diff --stat` showing the same 3-file/36-insertion/27-deletion shape as before the stash).
  Confirms no regression and no new diagnostic; nothing names any of the three touched files.

Inspection trace (per Verification):

- **radarr example** — confirmed via `git diff`: no `app: <scalar>` line, no `instance_name:` key, no
  nested `radarr:` block, no `proxmox:` block. Top-level keys present: `stack` (uncommented), `app`
  (uncommented), `routing` (commented) — exactly the allowed set, `update`/`proxmox` absent by
  design.
- **Consumed paths confirmed by grep** against `ansible/playbooks/apps/_template.yml`: line 63
  `app_config.stack`, line 70 `app_config.proxmox.network`, line 128 `app_config.app.port`, line 143
  `app_config.routing.auth` — matches the plan's Context grep evidence exactly. `stack:
  media_stack` → `app_config.stack`; `app.root_folder` → `app_config.app.root_folder` (within the
  `app_config.app.*` namespace read at `app_config.app.port` and elsewhere).
- **_template example** — app-specific block now teaches `# app:` not `# APP_NAME:`; filename-=-
  instance doctrine, commented `proxmox:`/`stack:` blocks, and commented `routing:` block all
  retained unchanged.
- **Shape parity with `vars/app-defaults/_template.yml`** — confirmed both examples' top-level keys
  (`proxmox`/`stack`, `app`, `update`, `routing`) mirror the defaults-template dict's keys
  (`APP_NAME_defaults.proxmox`/commented `stack`, `.app`, commented `.update`, `.routing`).
- **Config-layering compliance** — no uncommented empty-string/`0` value in either example; all
  optional keys remain commented.
- **CONTRACT.md** — confirmed via `git diff` (above): §2 row, §5 closing paragraph, and §6 row no
  longer say "contradictory/owned by 005/not resolved here"; App-level layering note now carries the
  "Instance-file schema (settled by slice 005)" paragraph stating filename = instance name, top-level
  keys mirror `<app>_defaults`, whole-file `combine(recursive=True)`, and the
  mapping-clobbered-by-scalar rule; the pre-existing "separate from the four-layer merge" paragraph
  is preserved verbatim above it.

No decisions needed — all four Context/Plan decisions (A-D) were already resolved in the plan text
and applied verbatim.

[reviewer] verdict: PASS
 - Verified all three file diffs match the plan's verbatim blocks byte-for-byte: radarr.example.yml full rewrite (no `app: <scalar>`, no `instance_name:`, no nested `radarr:`, no `proxmox:`; uncommented `stack`/`app.root_folder` only), _template.example.yml single-line `# APP_NAME:` → `# app:` change, CONTRACT.md's four edits (3a/3b/3c/3d) all match plan quotes exactly.
 - Confirmed top-level key shape parity against `vars/app-defaults/_template.yml` (`proxmox`|`stack`, `app`, `update`, `routing`) for both examples.
 - Diff scope confirmed exactly the three files (`git diff --stat`); `.claude/settings.local.json` M and untracked `.claude/agents/fable-groomer.md`/`.claude/autobuild/` predate this round per session's initial `git status`, consistent with implementer's note.
 - Config-layering compliance: no uncommented empty/0 values; all optional keys remain commented.
 - Verified the WSL path substitution is sound: `/mnt/c/Users/korr/source/repos/homelab-infra/.claude/gate/lint.sh` and `test.sh` exist at that mount (spot-checked, not a gate re-run) — same committed scripts as `build.yml` names, only the stale hardcoded `kevin/GitHub/hardKOrr` path corrected. Gate evidence is green and internally consistent (RC=0 both gates; test-gate's 3 `[ERROR]` diagnostics cross-checked against `reconcile-config-example.md`'s base evidence — the missing 4th "docker role not found" diagnostic is explained by prior unrelated work adding `roles/docker/`, and confirmed via the implementer's own stash-based base-vs-branch comparison). Did not re-run the gates myself since the evidence was complete, consistent, and green.
 - No reflex violations (secrets, injection, swallowed errors) — docs/example-only change.
 - No trivia fixes needed.
[qa] verdict: PASS
 - Senior pass: read the full three-file diff myself against the plan's verbatim blocks — exact
   match (radarr full rewrite; `# APP_NAME:` → `# app:` one-liner; CONTRACT.md edits 3a-3d with the
   pre-existing layering paragraph preserved). No schema claim contradicts the merge at
   `_template.yml:51-53`; `ansible/playbooks/apps/_template.yml` untouched as fenced.
 - Gate evidence accepted without a third run: implementer ran both gates (RC=0) with a stash-based
   base-vs-branch comparison showing byte-identical test diagnostics; reviewer independently
   verified the path substitution and evidence consistency. A docs-only diff outside the gates'
   parse targets cannot move them.
 - Lifted for design (not fixed here, out of scope): (1) `.claude/build.yml` gate commands hardcode
   the stale WSL path `/mnt/c/Users/kevin/GitHub/hardKOrr/homelab-infra`; actual mount is
   `/mnt/c/Users/korr/source/repos/homelab-infra` — fold the path fix into build.yml at the next
   korr-design pass. (2) The known-diagnostics baseline changed: "docker role not found" no longer
   occurs (roles/docker/ now exists); future plans should cite three pre-existing test-gate
   [ERROR]s, not four.
 - Meta slice 005's acceptance boxes are satisfied by this diff; CONTRACT.md now states the settled
   instance-file schema. Committing: three files + this plan (moved to done/), squashed on
   `fix/settle-instance-config-schema`, ff-merged to master.
