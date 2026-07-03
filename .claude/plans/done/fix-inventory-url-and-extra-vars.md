# fix-inventory-url-and-extra-vars

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/config-layering.md; review 2026-07-02 (related: meta 004 owns the
api_host/host key rename; meta 002 owns example-file reconciliation — this item is only the
inventory plugin's consumption of those keys)

## Context

Two defects keep the dynamic inventory from working on a fresh clone:

1. **No scheme in the URL** — `ansible/inventory/proxmox.yml:4` builds
   `url: "{{ api_host }}:{{ api_port }}"`. The `community.proxmox` inventory plugin expects a
   full URL (its default is `http://localhost:8006`), while the provisioning modules
   (`tasks/proxmox/lxc-create.yml:23`, `vm-create.yml:22`) take the same `api_host` value as a
   bare hostname. One value cannot be both. Decide the canonical shape (bare host in config is
   the smaller user surface) and derive the URL in the inventory file
   (`https://{{ api_host }}:{{ api_port | default(8006) }}`), or introduce a scheme key.
2. **Extra vars not available to the plugin** — the file templates `homelabinfra_config.*`
   (lines 4-7), which per its own comment (line 2) arrives via `-e @user-vars.yml`. Inventory
   plugin option templating only sees extra vars when `use_extra_vars` is enabled
   (`[inventory] use_extra_vars = True` in `ansible/ansible.cfg`, absent today) [unverified for
   this specific plugin version — the groomer/implementer must confirm against the installed
   community.proxmox docs once establish-ansible-gate lands].

Also note `validate_certs: false` at line 8 — acceptable for homelab Proxmox self-signed certs,
but worth a comment saying so deliberately.

Gate scope (from `.claude/build.yml` + `.claude/gate/lint.sh`): the lint scan target is
`playbooks roles tasks vars` — `inventory/` is **not** scanned, and both gates neutralise the
dynamic inventory via `ANSIBLE_INVENTORY=localhost,`. Gate green is therefore a non-regression
check only; proving these edits requires a direct `ansible-inventory` run (see Verification).

Adjacent known break, out of scope here: `config.example/proxmox.yml` writes `proxmox.host`/
`proxmox.port` while the inventory and modules read `api_host`/`api_port` — owned by meta 004
(key rename) and meta 002 (example reconciliation).

## Goal

Make `ansible/inventory/proxmox.yml` consume the user's Proxmox connection config correctly: a
schemed URL and extra-vars availability for plugin option templating.

> **Re-scoped 2026-07-03 (user-directed, see `[qa]` rounds in Run log):** the extra-vars half is
> split to backlog item `fix-inventory-extra-vars-delivery` — round 1 proved no `ansible.cfg`
> setting can deliver extra vars to this plugin's connection options. This item now covers only
> the schemed URL and the `validate_certs` justification.

## Acceptance criteria

- The inventory `url` option resolves to a full `https://host:port` URL from the same config keys
  the modules use (no second place for users to write the host).
- ~~`ansible/ansible.cfg` enables whatever setting the installed plugin version requires for
  extra-vars templating of inventory options, with a comment citing it — or, if the plugin
  version resolves options without it, the finding is recorded as not applicable in the Run
  log.~~ **Split 2026-07-03 → `fix-inventory-extra-vars-delivery`** (round 1 disproved both
  branches: the setting exists only as `[inventory_plugins] use_extra_vars` and never applies to
  this plugin's connection-option templating; `ansible.cfg` stays untouched in this item).
- `validate_certs: false` carries a one-line justification comment.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

Two files change, both surgical. No playbook, task, role, or example file is touched (meta 004 owns
the `api_host`/`host` key rename; meta 002 owns example-file reconciliation). Work test-first: run
the differential inventory check in Verification, apply the edits, re-run until it flips from the
undefined-variable error to a connection error, then confirm the `lint`/`test` gates are unchanged.

### Step 1 — `ansible/inventory/proxmox.yml` (edit, exists)

The file already reads `homelabinfra_config.proxmox.api_host` / `api_port` — the same canonical keys
the modules use (`tasks/proxmox/lxc-create.yml:23`, `vm-create.yml:22`). Do **not** change those key
names. Make exactly two edits:

1. **Add the `https://` scheme to the `url` option** (line 4). Replace:

   ```yaml
   url: "{{ homelabinfra_config.proxmox.api_host }}:{{ homelabinfra_config.proxmox.api_port | default(8006) }}"
   ```

   with a commented, schemed derivation:

   ```yaml
   # Derive a full URL (scheme + host + port) from the same bare api_host the provisioning modules
   # consume, so users configure the host in exactly one place. community.proxmox's inventory `url`
   # option expects a scheme (its default is http://localhost:8006); port falls back to 8006.
   url: "https://{{ homelabinfra_config.proxmox.api_host }}:{{ homelabinfra_config.proxmox.api_port | default(8006) }}"
   ```

2. **Add a one-line justification above `validate_certs: false`** (line 8). Replace:

   ```yaml
   validate_certs: false
   ```

   with:

   ```yaml
   # Homelab Proxmox nodes use self-signed TLS certs; skip verification deliberately.
   validate_certs: false
   ```

Leave every other line (plugin, user, token_id, token_secret, want_facts, groups, keyed_groups)
byte-for-byte unchanged.

### Step 2 — `ansible/ansible.cfg` (edit, exists — dossier's "create if absent" is stale)

The file exists with `[defaults]`, `[privilege_escalation]`, `[ssh_connection]` sections. Append a
new `[inventory]` section (order after the existing sections is fine — INI section order is not
significant):

```ini
[inventory]
# inventory/proxmox.yml templates its connection options from homelabinfra_config.*, which arrives
# via `-e @user-vars.yml` (Rundeck/Semaphore pass it that way). Ansible inventory plugins only merge
# extra vars into option templating when this is enabled; without it the plugin renders url/user/
# token_* against an undefined homelabinfra_config and fails. See plan Verification for the
# differential check that confirms this against the installed community.proxmox 2.0.0.
use_extra_vars = True
```

### Files touched

- `ansible/inventory/proxmox.yml` (edit) — schemed `url`, comment on `validate_certs`.
- `ansible/ansible.cfg` (edit) — new `[inventory] use_extra_vars = True`.

Nothing else. If detailing reveals the `host`/`api_host` example mismatch needs fixing to make a
fresh clone work end-to-end, that is meta 002/004's scope — do not pull it into this item.

## Decisions

- **Canonical config shape → keep the bare `api_host`/`api_port` keys, derive the URL in the
  inventory.** The provisioning modules take `api_host` as a bare hostname and the inventory needs a
  full URL; one value cannot be both. Deriving `https://{{ api_host }}:{{ api_port | default(8006) }}`
  inside the inventory keeps the host in one place (smaller user surface) and matches spec
  config-layering.md's canonical `api_host`/`api_port`. Rejected: adding a separate scheme/url key
  (second place for users to write the host).
- **Scheme → `https://` hardcoded, not configurable.** Proxmox's API is HTTPS on 8006 by default and
  this platform manages only what it creates; a plaintext-HTTP Proxmox API is not a homelab shape
  worth a config knob. Matches the plugin's expectation of a schemed URL.
- **Port → `| default(8006)`** (kept from the existing line). 8006 is Proxmox's fixed API port;
  keeping the default lets users omit `api_port` entirely.
- **Extra-vars availability → add `[inventory] use_extra_vars = True` to `ansible/ansible.cfg`.** This
  is documented ansible-core behavior: inventory plugins merge `-e` extra vars into option templating
  only when this is set. Without it the inventory's `homelabinfra_config.*` references are undefined at
  plugin load. Adding it is the correct, safe setting for this design (Rundeck/Semaphore feed config
  via `-e @user-vars.yml`) with no downside. The dossier's `[unverified]` flag is resolved by the
  differential check in Verification, which the implementer runs during the build — a resolvable
  engineering step, not a human decision. The acceptance criterion's "not applicable" branch only
  applies if that check shows the plugin resolves options without the setting (contradicts core
  behavior, not expected); if so, remove the section and record the finding in the Run log.
- **`ansible.cfg` is edited, not created.** The dossier said "create if absent"; the file exists
  (`[defaults]`/`[privilege_escalation]`/`[ssh_connection]`). Append the `[inventory]` section.
- **`validate_certs: false` → keep, add a one-line justification comment.** Homelab Proxmox uses
  self-signed certs; verification would break the connection. Decision is to document, not change,
  the behavior — satisfies the acceptance criterion's comment requirement.
- **Out of scope, deliberately not touched:** `config.example/proxmox.yml` writes `proxmox.host`/
  `proxmox.port` while the inventory and modules read `api_host`/`api_port` — a real fresh-clone
  break, but owned by meta 004 (key rename) and meta 002 (example reconciliation). This item only
  fixes the inventory plugin's consumption of the already-canonical keys.

## Verification

### Implementer proves

1. **Differential inventory check — the load-bearing proof.** The `lint`/`test` gates neutralise the
   dynamic inventory (`ANSIBLE_INVENTORY=localhost,`), so they do **not** exercise these edits. Prove
   the fix directly with `ansible-inventory` and a throwaway fake-creds vars file (no real secrets, not
   committed). Write to the scratchpad, e.g. `/tmp/fake-vars.yml`:

   ```yaml
   homelabinfra_config:
     proxmox:
       api_host: "127.0.0.1"
       api_token_id: "fake"
       api_token_secret: "fake"
   ```

   Run from `ansible/` in WSL, using the gate venv and this repo's `ansible.cfg`:

   ```bash
   ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg \
     "$HOME/.venvs/homelab-ansible/bin/ansible-inventory" \
     -i inventory/proxmox.yml -e @/tmp/fake-vars.yml --list
   ```

   - **Expected WITH the fix:** templating succeeds and the plugin attempts to reach
     `https://127.0.0.1:8006`, failing with a **connection/refused/auth** error — not an
     undefined-variable error. This proves the `url` rendered with scheme+host+port *and* that extra
     vars reached option templating. Capture the error text in the Run log.
   - **Confirm the setting is load-bearing:** re-run the same command with
     `ANSIBLE_INVENTORY_USE_EXTRA_VARS=False` prepended (env override of the cfg setting). Expected:
     `'homelabinfra_config' is undefined` (or equivalent undefined-variable error). The flip between
     the two runs is the proof that `use_extra_vars` is required for this plugin version. Record both
     outcomes in the Run log. (If the `False` run *also* resolves the vars, the setting is not needed
     for community.proxmox 2.0.0 — remove the `[inventory]` section and record "not applicable" per
     the acceptance criterion.)

2. **`lint` gate exits 0, no regression.** Run the exact build.yml command:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'` →
   expect `Passed: 0 failure(s), 0 warning(s) on 62 files`, exit 0. (`inventory/` is not in the lint
   scan target `playbooks roles tasks vars`, so this is a non-regression check confirming the
   `ansible.cfg` edit did not break config loading, not a direct scan of the touched files.)

3. **`test` gate unchanged.** Run
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'` →
   expect exit 1 with exactly the three pre-existing known-defect playbooks failing
   (`maintenance/restart-app.yml`, `maintenance/tail-applog.yml` — meta 102;
   `stacks/rollback-container.yml` — meta 502). This item touches no playbook, so nothing else moves.

4. **YAML validity of both edited files** confirmed (the `ansible-inventory` run in step 1 parses
   `inventory/proxmox.yml`; a clean gate run parses `ansible.cfg`).

### korr-qa senior pass confirms

- `ansible/inventory/proxmox.yml` `url` renders a full `https://host:port` from `api_host`/`api_port`
  (no scheme-less value, no second host key introduced), and `validate_certs: false` carries the
  justification comment.
- `ansible/ansible.cfg` carries `[inventory] use_extra_vars = True` with the citing comment — or, if
  the differential check showed it unnecessary, the Run log records "not applicable" and the section
  is absent.
- The differential `ansible-inventory` transcript shows the connection-error / undefined-variable
  flip, independently reproducible.
- `git diff` touches only `ansible/inventory/proxmox.yml` and `ansible/ansible.cfg` — no example
  files, no key rename, no playbook/task/role change (meta 002/004 scope intact).
- `lint` exits 0 (62 files) and `test` exits 1 on only the three documented known-defect playbooks.

## Run log

### [implementer] round 1 — 2026-07-02

(decision needed: defect #2's designed fix — `[inventory] use_extra_vars = True` in
`ansible.cfg` — does not work with the installed community.proxmox 2.0.0 / ansible-core 2.18.1,
and no ansible.cfg setting fixes it. See findings below. Step 1 (schemed URL, validate_certs
comment) is implemented and verified. Step 2 was implemented, disproven by test, and reverted —
`ansible.cfg` is unchanged from its pre-round state.)

**Branch:** `fix/fix-inventory-url-and-extra-vars`, created off `master` (the dependency
`establish-ansible-gate`'s run branch `feat/establish-ansible-gate` is an ancestor of `master` —
`fb86ee4` — so `master` is the superset base; branching off the dep branch directly would have
lost `decide-multinode-scoping` and the workspace commit already on top of it).

**Environment note (not a repo change):** the gate venv (`~/.venvs/homelab-ansible`) was missing
`requests` and `proxmoxer`, which `community.proxmox`'s inventory plugin requires
(`ansible-inventory` failed with "This module requires Python Requests 1.1.0 or higher" before
even reaching variable templating). Installed both with
`~/.venvs/homelab-ansible/bin/pip install requests proxmoxer` — local WSL venv only, no repo
file touched, `.claude/gate/requirements-dev.txt` unchanged (lint/test gates never invoke this
plugin, so they don't need these packages; only my differential check does).

**Step 1 — `ansible/inventory/proxmox.yml`: implemented, matches plan exactly.**
- Added `https://` scheme to `url`, with the plan's comment, keeping `api_host`/`api_port` keys
  and the `| default(8006)` fallback unchanged.
- Added the one-line justification comment above `validate_certs: false`.
- All other lines (plugin, user, token_id, token_secret, want_facts, groups, keyed_groups)
  byte-for-byte unchanged, confirmed via `git diff`.

**Step 2 — `ansible/ansible.cfg`: NOT carried — proven ineffective, reverted.**

Baseline (pre-fix) differential check, `ansible/inventory/proxmox.yml` unmodified,
`ANSIBLE_CONFIG` pointed at the repo's `ansible.cfg` (no `[inventory]` section yet):
```
ANSIBLE_CONFIG=.../ansible/ansible.cfg ansible-inventory -i inventory/proxmox.yml \
  -e @/tmp/korr-scratch/fake-vars.yml --list
...
[WARNING]:  * Failed to parse .../inventory/proxmox.yml with auto plugin: 'homelabinfra_config' is undefined
```
Matches the plan's expected pre-fix / `ANSIBLE_INVENTORY_USE_EXTRA_VARS=False` outcome.

Applied Step 1 + Step 2 exactly as the plan specifies (`[inventory] use_extra_vars = True`).
Re-ran the same command: **error unchanged** — still `'homelabinfra_config' is undefined`. The
expected flip to a connection error did not occur.

Investigated why, reading the installed plugin/ansible-core source
(`~/.venvs/homelab-ansible/lib/python3.12/site-packages/ansible/plugins/inventory/__init__.py`,
`.../ansible/plugins/doc_fragments/constructed.py`,
`~/.ansible/collections/ansible_collections/community/proxmox/plugins/inventory/proxmox.py`):

1. `use_extra_vars` is not an ansible-core `[inventory]` setting at all — it's a per-plugin
   option contributed by the `constructed` doc fragment (`ansible-config list` on this
   ansible-core 2.18.1 has no `extra_vars`-named setting under `[inventory]`; confirmed via
   `grep -i extra_vars` over the full `ansible-config list` output — zero matches). Its actual
   ini location is `[inventory_plugins] use_extra_vars` (env `ANSIBLE_INVENTORY_USE_EXTRA_VARS`),
   per `constructed.py`'s doc fragment. The plan's `[inventory]` section name is wrong.
2. More importantly, **the setting doesn't apply to this code path regardless of section name.**
   `use_extra_vars` only gates the `Constructable` mixin's merge of extra vars into
   `self.templar.available_variables` for `compose`/`groups`/`keyed_groups` templating
   (`plugins/inventory/__init__.py` ~line 336-346). `community.proxmox.proxmox.parse()` templates
   `url`/`user`/`password`/`token_id`/`token_secret` directly
   (`self.templar.template(v)` at `proxmox.py:693`) using `self.templar`, whose
   `available_variables` is set once in the base class's `parse()`
   (`self.templar = Templar(loader=loader)` — defaults to `{}`) and is **never** populated with
   `self._vars` (the loaded extra vars) before this call. `use_extra_vars` is never read on this
   path.
3. Proved this empirically, not just by reading source: retested with the ansible-core-correct
   section name, `[inventory_plugins]\nuse_extra_vars = True`, against both an isolated
   single-level extra var (`test_var`, ruling out a `homelabinfra_config`-specific namespacing
   issue) and the real `inventory/proxmox.yml` + fake-vars file. Both still raise
   `'test_var' is undefined` / `'homelabinfra_config' is undefined` respectively, with the
   traceback pointing at `proxmox.py:693`.

Given this, `[inventory] use_extra_vars = True` (or the corrected `[inventory_plugins]` form)
does not satisfy defect #2 or the acceptance criterion for *any* section name — it does not
"resolve options without it" (the acceptance criterion's only documented fallback) either; it
simply never works for this plugin's connection options. Rather than leave a comment in
`ansible.cfg` asserting a mechanism that's proven false, or duplicate scope by inventing an
undesigned alternative (e.g. switching to the plugin's `PROXMOX_URL`/`PROXMOX_USER`/
`PROXMOX_TOKEN_ID`/`PROXMOX_TOKEN_SECRET` environment-variable fallback, which would require
something upstream of `ansible-inventory` — a wrapper script or job-runner step — to export those
vars from `homelabinfra_config`, a design change beyond this plan's two-file scope), I reverted
`ansible.cfg` to its original three-section content. `git diff` confirms it is byte-identical to
`master`.

**Decision needed for a follow-up round/plan:** how to make `homelabinfra_config.proxmox.*`
reach the community.proxmox inventory plugin's connection options. Options observed, not chosen:
(a) export `PROXMOX_URL`/`PROXMOX_USER`/`PROXMOX_TOKEN_ID`/`PROXMOX_TOKEN_SECRET` env vars from
whatever loads `homelabinfra_config` before invoking `ansible-inventory`/`ansible-playbook`
(Rundeck/Semaphore job step, or a wrapper script) — the plugin's own documented fallback path;
(b) track as an upstream community.proxmox limitation/bug and accept it as a known defect outside
this repo's control for now; (c) something else. This is a real design choice with tradeoffs, not
a guessable one-liner — leaving it for korr-qa's senior pass / a follow-up plan per Decisions and
Exceptions guidance.

**Verification evidence:**

1. Differential inventory check (load-bearing) — captured above. Pre-fix and post-Step-1-only
   (Step 2 reverted) both correctly show `'homelabinfra_config' is undefined`, confirming Step 1
   alone doesn't regress anything and Step 2 genuinely contributes nothing on this plugin version
   (proven, not assumed).
2. `lint` gate:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
   → exit 0, `Passed: 0 failure(s), 0 warning(s) on 62 files. Profile 'min' was required, but
   'production' profile passed.` Matches expected, no regression.
3. `test` gate:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
   → exit 1, with exactly the three documented known-defect playbooks failing:
   `playbooks/maintenance/restart-app.yml` (`'instance' is undefined` — meta 102),
   `playbooks/maintenance/tail-applog.yml` (`'instance' is undefined` — meta 102),
   `playbooks/stacks/rollback-container.yml` (`Empty playbook, nothing to do` — meta 502). No
   other playbook affected. Matches expected, no regression.
4. YAML validity: `inventory/proxmox.yml` parses (the differential `ansible-inventory` run gets
   past YAML/plugin-name validation into option templating, where it fails only on the
   pre-existing undefined-variable defect); `ansible.cfg` parses cleanly (`ansible-config dump`
   and both gate runs load it without error) — confirmed unchanged from its committed state.

**Files touched:** `ansible/inventory/proxmox.yml` (edit — schemed `url`, `validate_certs`
comment). `ansible/ansible.cfg` not changed (edit attempted, disproven, reverted — see above).

**Scratchpad artifacts (not committed, not in repo):** fake-creds vars file at
`/tmp/korr-scratch/fake-vars.yml` inside WSL (contains only `127.0.0.1`/`fake`/`fake` — no real
secrets).

### [qa] round 1 — 2026-07-03

Blocked after 1 round; no reviewer fired (the diff cannot meet the acceptance criteria as
written, so a review verdict is a foregone conclusion).

**Ruling on the implementer's decision flag:** the plan's designed mechanism
(`use_extra_vars` in `ansible.cfg`) is disproven at source level and empirically for
community.proxmox 2.0.0 / ansible-core 2.18.1 — it gates only `Constructable`
compose/groups/keyed_groups templating, never the plugin's direct `url`/`user`/`token_*`
templating (`proxmox.py:693`, empty-`available_variables` Templar). Acceptance criterion 2's two
branches (setting works / plugin resolves without it) are both false; reality is a third case the
plan did not anticipate. Every workable alternative — (a) exporting
`PROXMOX_URL`/`PROXMOX_USER`/`PROXMOX_TOKEN_ID`/`PROXMOX_TOKEN_SECRET` from the job-runner or a
wrapper before `ansible-inventory` runs, (b) a bootstrap-rendered static inventory, (c) accepting
an upstream limitation — changes the config-delivery design and intersects
`specs/config-layering.md` plus meta 002/004 scope. That is a re-groom, not a round-2 fix.

**Recommendation for the re-groom:** option (a), the plugin's documented env-var fallback, fits
the existing Semaphore/Rundeck job model (both already pass `-e @user-vars.yml`; adding env
exports in the same job step is the smallest surface) and keeps `config/proxmox.yml` as the
single source. The inventory file's templated connection options would then be removed or made
env-backed rather than extra-vars-backed.

**Salvage:** Step 1 (schemed `https://` url derivation + `validate_certs` justification comment)
is correct, verified non-regressive (lint 0/62, test gate unchanged on the three known-defect
playbooks), and independent of the blocked half. It stays uncommitted in the working tree on
`fix/fix-inventory-url-and-extra-vars` for the re-groomed plan (or a resumed run) to carry
forward.

### [qa] round 2 — 2026-07-03

**Re-scope, user-directed — supersedes the round-1 block.** The user chose to split the
disproven extra-vars half into backlog item `fix-inventory-extra-vars-delivery` (created, carries
the round-1 findings and the env-var-fallback recommendation, depends on this item) and drive the
verified remainder to commit. Plan Goal and acceptance criterion 2 amended above with dated
markers; this item now covers only the schemed `url` derivation, the `validate_certs`
justification comment, and gate non-regression — all already implemented and evidenced in round
1. Proceeding to review against the amended criteria.

### [reviewer] verdict: PASS

Checked `ansible/inventory/proxmox.yml` diff against the amended criteria: (1) `url` now
`https://{{ homelabinfra_config.proxmox.api_host }}:{{ homelabinfra_config.proxmox.api_port |
default(8006) }}` — same `api_host`/`api_port` keys the modules use, no second host key; (2)
`ansible/ansible.cfg` confirmed byte-identical to master (`git diff master -- ansible/ansible.cfg`
empty); (3) `validate_certs: false` carries the one-line self-signed-cert justification comment;
all other lines (plugin, user, token_id, token_secret, want_facts, groups, keyed_groups) confirmed
byte-for-byte unchanged. Re-ran both gates independently rather than trusting the pasted evidence:
`lint.sh` → exit 0, `Passed: 0 failure(s), 0 warning(s) on 62 files`, matches round 1 exactly;
`test.sh` → exit 1, failing on exactly the three documented known-defect playbooks
(`restart-app.yml`, `tail-applog.yml` — undefined `instance`; `rollback-container.yml` — empty
playbook). Reflex check clean: no secrets/tokens hardcoded (values remain templated from user
config), no injection surface in this file, no error-handling to swallow.

**Finding (non-blocking):** the working tree also carries a `.claude/settings.local.json` change
(two new permission-list entries: a `gh` command-source lookup and a `usage_today.py` scratchpad
invocation) unrelated to this plan's declared "Files touched" and to any other active plan work
visible here. It's permission-grant strings only — no secrets, no code — and reads as harness
session accumulation rather than a deliberate scope addition, but it doesn't belong to this plan's
diff. Flagging for whoever commits: either drop it from this commit or confirm it's intentional
housekeeping.

### [qa] verdict: PASS

Senior pass on round 2 (re-scoped). Independently read the diff earlier this session: matches
the plan's Step 1 byte-for-byte, `ansible.cfg` byte-identical to master, no scope creep into
meta 002/004 territory. Round-1 evidence is source-level and empirical, reviewer re-ran both
gates independently with identical results. Reviewer's non-blocking finding resolved: the
`.claude/settings.local.json` diff is user-directed session housekeeping from this QA session
(permission-list cleanup) — deliberately excluded from this plan's squash commit; the user
commits workspace files separately (precedent: b80268e). Committing: `ansible/inventory/
proxmox.yml`, the plan file (active → done, full Run log), the split-off backlog dossier
`fix-inventory-extra-vars-delivery.md`, and the removal of the plan's stale backlog copy.
