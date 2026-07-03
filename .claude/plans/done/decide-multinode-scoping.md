# decide-multinode-scoping

**Type:** refactor

**Depends on:** —

**Spec:** .claude/architecture.md ("Cross-play handoff" and "Proxmox boundary" seams);
review 2026-07-02

## Goal

Decide and implement one consistent execution model for provisioning plays: today they target
`hosts: proxmox_nodes` with `run_once` facts, which breaks on any Proxmox cluster with more than
one node.

## Context

`tasks/load-user-vars.yml` sets `homelabinfra_config` with `run_once: true` — `set_fact` +
`run_once` sets the fact on **one** host only. With two or more nodes in `proxmox_nodes`, every
other node lacks the config and any task touching `homelabinfra_config` on those hosts fails.
Conversely, tasks *without* `run_once` (all of `tasks/proxmox/lxc-create.yml` / `vm-create.yml`)
would run once per node — duplicate create attempts with the same vmid.

Two coherent models; the choice shapes every provisioning playbook:

- **A. Localhost/API model (recommended for evaluation first):** `community.proxmox.proxmox` and
  `proxmox_kvm` are API clients — run provisioning plays on `localhost` and delegate only the
  node-local `pct`/`qm` waits (lxc-create.yml:78-103, vm-create.yml:82-107) to the specific node
  named in `homelabinfra_config.proxmox.node` via `delegate_to`. Removes the whole class of
  fact-scoping bugs; multi-node clusters work naturally.
- **B. Declared single-node assumption:** keep `hosts: proxmox_nodes`, document that exactly one
  node is supported, and add an assert (`groups['proxmox_nodes'] | length == 1`) so a cluster
  user gets a clear message instead of silent fact corruption.

Affected files: `playbooks/proxmox/create-lxc.yml`, `create-vm.yml`,
`playbooks/docker/create-docker-host.yml`, `playbooks/apps/_template.yml` (Play 1),
`tasks/load-user-vars.yml` (the run_once pattern), and the `pct`/`qm` wait/exec tasks in
`tasks/proxmox/*.yml`. The repo philosophy ("defaults cover 80% of homelabs") tolerates B, but
the repo is meant to be shared and clusters are common in homelabs; A is more work now, less
support burden forever.

This is a direction decision for the human before grooming: **A or B?**

## Acceptance criteria

- One model is chosen, recorded in `.claude/architecture.md` (Proxmox boundary seam), and applied
  to all provisioning playbooks — no play remains that both targets `proxmox_nodes` and relies on
  `run_once` facts.
- Model A: provisioning plays run on localhost; `pct`/`qm` tasks are delegated to the configured
  node. Model B: a single-node assert with a friendly message runs before any provisioning task.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

Apply **Model A** to every provisioning play: the play host becomes `localhost` (it is only an API
client), and the node-local `pct`/`qm` shell tasks are `delegate_to` the node named in
`homelabinfra_config.proxmox.node`. The API-connection vars already live inside the module-arg dicts
built from `homelabinfra_config.proxmox.*` (`api_host`, `api_token_id`, `api_token_secret`, `node`),
so nothing about *how the API is reached* changes — only *which host the play runs on*. `delegate_to`
does not change variable resolution (delegate_facts stays default-false), so the delegated `pct`/`qm`
tasks still read `homelabinfra_instance.*` from localhost's facts.

Touch eight files. No test-first unit here (these are playbooks, not modules); the executable proof is
the two committed gates (see `## Verification`). Make exactly these edits, nothing else.

### 1. `ansible/tasks/load-user-vars.yml` — remove the `run_once` pattern

Delete `run_once: true` from all three tasks that carry it:
- "Load homelabinfra defaults" (currently line 12)
- "Load user vars from file option" (currently line 18)
- "Merge defaults with user config" (currently line 26)

Rationale: `set_fact` + `run_once` writes `homelabinfra_config` onto **one** host only — the multi-node
bug. On the now-single-host `localhost` provisioning plays the removal is behaviorally a no-op; on the
guest deploy plays that also import this file it becomes correct (each guest merges its own config).
The `combine(recursive=True)` merge expression itself is unchanged — keep it exactly as-is.

### 2. `ansible/playbooks/proxmox/create-lxc.yml` — retarget to localhost

- `hosts: proxmox_nodes` → `hosts: localhost`
- `gather_facts: true` → `gather_facts: false`
- Keep `become: false`. Leave every task body unchanged (the `pct` waits it calls live in file 6).

### 3. `ansible/playbooks/proxmox/create-vm.yml` — retarget to localhost

- `hosts: proxmox_nodes` → `hosts: localhost`
- `gather_facts: true` → `gather_facts: false`
- Keep `become: false`. Task bodies unchanged (the `qm` waits live in file 7).

### 4. `ansible/playbooks/docker/create-docker-host.yml` — retarget Play 1, drop `run_once`

Only **Play 1** ("Create docker host") changes. Plays 2 and 3 (`hosts: provisioning`) are guest plays —
leave them fully unchanged.

- `hosts: proxmox_nodes` → `hosts: localhost`
- `gather_facts: true` → `gather_facts: false`
- Keep `become: false`.
- Remove `run_once: true` from all five Play-1 tasks that carry it:
  - "Set docker host selector"
  - "Assert docker host config exists"
  - "Set docker host type"
  - "Ensure LXC keyctl feature for docker hosts"
  - "Add docker host to inventory"
- Leave the keyctl `combine(recursive=True)` expression and all `when:` guards exactly as-is.

### 5. `ansible/playbooks/apps/_template.yml` — retarget Play 1, fix its header comment

Only **Play 1** ("APP_NAME | Provision") changes. Plays 2 and 3 unchanged.

- `hosts: proxmox_nodes` → `hosts: localhost`
- `gather_facts: true` → `gather_facts: false`
- Keep `become: false`.
- Replace the two-line comment above the play (currently "Runs against the Proxmox node to provision
  or locate the guest. / Adds the target host to a group for Play 2 to use.") with:
  "Runs on localhost against the Proxmox API to provision or locate the guest. The node-local
  pct/qm waits inside lxc-create.yml / vm-create.yml delegate to homelabinfra_config.proxmox.node.
  Adds the target host to a group for Play 2 to use."
- Leave PATH A / PATH B task blocks unchanged (the commented PATH B `add_host` already keys off
  `homelabinfra_instance.network.ip_address`, which is a localhost fact — correct under Model A).

### 6. `ansible/tasks/proxmox/lxc-create.yml` — delegate the `pct` tasks only

Add one line, `delegate_to: "{{ homelabinfra_config.proxmox.node }}"`, to each of the three node-local
tasks — and to **only** these three:
- "Wait for LXC to be running" (the `pct status` command)
- "Wait for LXC to accept exec" (the `pct exec ... /bin/true` command)
- "Write LXC instance data to /root/home" (the `pct exec` heredoc shell)

Do **not** add `delegate_to` to "Create LXC container" (the `community.proxmox.proxmox` task) — that is
an API call and must stay on localhost. `set_fact` fact-builder tasks stay on localhost too.

Pseudo-code for each delegated task (illustrative — keep the existing `cmd`/`register`/`retries`/
`delay`/`until`/`changed_when` verbatim, only append the delegate line):

```yaml
- name: Wait for LXC to be running
  ansible.builtin.command:
    cmd: "pct status {{ homelabinfra_instance.lxc.vmid }}"
  delegate_to: "{{ homelabinfra_config.proxmox.node }}"
  register: lxc_status
  retries: 30
  delay: 2
  until: "'status: running' in lxc_status.stdout"
  changed_when: false
```

### 7. `ansible/tasks/proxmox/vm-create.yml` — delegate the `qm` tasks only

Same edit, three tasks:
- "Wait for VM to be running" (`qm status`)
- "Wait for VM guest agent to accept exec" (`qm guest exec ... /bin/true`)
- "Write VM instance data to /root/home" (`qm guest exec` heredoc shell)

Do **not** delegate "Create VM" (`community.proxmox.proxmox_kvm`) — API call, stays on localhost.

### 8. `ansible/architecture.md` — correct the stale flow diagram only

The Proxmox-boundary seam already records Model A (leave lines 73-79 as-is). Only the **Flows** section
still describes the old model; correct the two spots that name the Play-1 host:
- In "App deploy" step 1: change "against `proxmox_nodes`" to "on `localhost`".
- In the ASCII diagram: change `[Play 1: proxmox node]` to `[Play 1: localhost]`.

Do not otherwise reflow or reword the Flows section.

### Out of scope (do not touch)

- `ansible/tasks/network/generate-ip.yml` — not in the dossier's affected-files list. Its `run_once`
  becomes a harmless no-op on single-host localhost plays, and its known bare-`set_fact` violation is
  owned by meta slice 006. Leave it entirely.
- Plays 2/3 of the app template and docker-host playbook (guest and wiring plays) — already correct.

## Decisions

- RESOLVED (KOrr, 2026-07-02): **Model A** — localhost/API model with `pct`/`qm` waits delegated
  to the configured node. Rationale: user already runs a multi-node cluster; Model B would make
  the platform unusable for them from day one.
- Play-host header for provisioning plays → `hosts: localhost`, `gather_facts: false`, `become:
  false`. Why: mirrors the existing localhost-API convention already used by the template's
  Play 3 (wiring); the API modules need no node facts, and localhost has network reach to the
  Proxmox API. `gather_facts: false` because nothing reads node/local `ansible_facts`.
- No explicit `connection: local`. Why: match the repo pattern — the template's Play 3 uses bare
  `hosts: localhost` and relies on Ansible's implicit-localhost local connection. Adding a second
  style here would violate the "match the established pattern" rule.
- `delegate_to` target = `homelabinfra_config.proxmox.node` (a Jinja string, used only as a
  delegation host name — no numeric casting, so jinja-string-typing is satisfied). Why: this is the
  same node the old `hosts: proxmox_nodes` play SSH'd into to run `pct`/`qm`; the `community.proxmox`
  dynamic inventory names node entries by node name, so the value resolves to a reachable
  `proxmox_nodes` host. Delegation preserves the pre-existing SSH-to-node path exactly.
- Delegated tasks keep `become: false` (inherited from play). Why: preserves prior behavior —
  the old model ran `pct`/`qm` as the node's inventory SSH user (root on Proxmox nodes); delegation
  connects as that same user. No privilege change is introduced by this refactor.
- Only the three `pct`/`qm` shell/command tasks per file are delegated; the
  `community.proxmox.proxmox`/`proxmox_kvm` tasks and all `set_fact` fact-builders stay on localhost.
  Why: the modules are API clients and the facts are localhost-scoped; delegating them would move
  the API call onto a node needlessly and (for facts) is meaningless.
- API-connection vars need no plumbing change. Why: `api_host`/`api_port`/`api_token_id`/
  `api_token_secret`/`node` are already folded into `homelabinfra_instance.{lxc,vm}` from
  `homelabinfra_config.proxmox.*` by the fact-builder in lxc/vm-create; that dict is passed whole as
  the module args and now simply executes on localhost.
- `run_once` removed rather than kept-as-no-op. Why: the acceptance criterion is that no play both
  targets `proxmox_nodes` and relies on `run_once` facts; removing the keyword closes the pattern
  outright and also fixes the latent per-guest-config bug in the reused `load-user-vars.yml`.
- architecture.md edit scope = flow diagram + step-1 host label only. Why: the Proxmox-boundary
  seam already records Model A (dossier confirms); the dossier explicitly flags only the stale
  `[Play 1: proxmox node]` diagram as needing correction.

## Verification

Both gates are the committed wrappers from `.claude/build.yml`; run them from the repo root.

1. **`lint` gate** (the acceptance-criteria gate) — `.claude/build.yml`'s `lint:`:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
   Must pass clean over `playbooks roles tasks vars`, covering all seven touched Ansible files.
   Introduces no new ansible-lint findings (in particular no new `no-changed-when` / delegation
   warnings on the delegated `pct`/`qm` tasks — they retain `changed_when: false`).

2. **`test` gate** (syntax-check) — `.claude/build.yml`'s `test:`:
   `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
   `--syntax-check` must pass for every playbook, confirming the retargeted `hosts:` and the added
   `delegate_to:` lines parse in `create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml`, and
   `_template.yml`.

korr-qa senior pass confirms, by inspection:
- **No residual `proxmox_nodes` + `run_once` pattern.** No provisioning play targets
  `hosts: proxmox_nodes`; `grep` for `run_once` in `load-user-vars.yml` and Play 1 of
  `create-docker-host.yml` returns nothing.
- **Delegation is exact.** Exactly the three `pct` tasks (lxc-create.yml) and three `qm` tasks
  (vm-create.yml) carry `delegate_to: "{{ homelabinfra_config.proxmox.node }}"`; the
  `community.proxmox.proxmox` / `proxmox_kvm` API tasks do **not**.
- **Namespace discipline intact.** Every `homelabinfra_config` / `homelabinfra_instance` write left
  in the touched files still uses `combine(recursive=True)`; no bare namespace assignment was
  introduced (namespace-merge-discipline).
- **Cross-play handoff unchanged.** `add_host` remains the only cross-play state carrier; hostvars
  passed to the `app_deploy` / `provisioning` groups are untouched.
- **architecture.md matches reality.** The Flows section (step 1 + ASCII diagram) now names
  `localhost` for Play 1, consistent with the already-recorded Proxmox-boundary seam.

## Run log

### [implementer] round 1 — 2026-07-02

Applied Model A to all eight files exactly per `## Plan`. Files touched:

1. `ansible/tasks/load-user-vars.yml` — removed `run_once: true` from the three tasks
   ("Load homelabinfra defaults", "Load user vars from file option", "Merge defaults with
   user config"). The `combine(recursive=True)` expression is unchanged in logic; note the
   Edit tool trimmed three trailing-space characters at line-ends inside that expression
   (whitespace only, no token/semantic change — confirmed via `git diff`).
2. `ansible/playbooks/proxmox/create-lxc.yml` — `hosts: proxmox_nodes` → `hosts: localhost`,
   `gather_facts: true` → `false`. `become: false` unchanged. Task bodies untouched.
3. `ansible/playbooks/proxmox/create-vm.yml` — same retarget as above.
4. `ansible/playbooks/docker/create-docker-host.yml` — Play 1 only: retargeted to
   `localhost`/`gather_facts: false`; removed `run_once: true` from all five Play-1 tasks
   ("Set docker host selector", "Assert docker host config exists", "Set docker host type",
   "Ensure LXC keyctl feature for docker hosts", "Add docker host to inventory"). Plays 2/3
   (`hosts: provisioning`) left byte-for-byte unchanged.
5. `ansible/playbooks/apps/_template.yml` — Play 1 only: retargeted to
   `localhost`/`gather_facts: false`; replaced the two-line header comment with the
   three-line version specified in the plan. Plays 2/3 unchanged; PATH A/PATH B task
   blocks unchanged.
6. `ansible/tasks/proxmox/lxc-create.yml` — added
   `delegate_to: "{{ homelabinfra_config.proxmox.node }}"` to exactly the three `pct`
   tasks ("Wait for LXC to be running", "Wait for LXC to accept exec", "Write LXC instance
   data to /root/home"). "Create LXC container" (`community.proxmox.proxmox`) and the
   `set_fact` fact-builders were left on localhost, undelegated.
7. `ansible/tasks/proxmox/vm-create.yml` — same edit, three `qm` tasks ("Wait for VM to be
   running", "Wait for VM guest agent to accept exec", "Write VM instance data to
   /root/home"). "Create VM" (`community.proxmox.proxmox_kvm`) left undelegated.
8. `.claude/architecture.md` — confirmed this is the file that exists (`ansible/architecture.md`
   does not exist; the plan's file-8 heading is stale, the Spec line's path is correct). Lines
   73-79 (Proxmox-boundary seam, already recording Model A) left as-is. Corrected only the two
   stale Flows-section spots: App-deploy step 1 "against `proxmox_nodes`" → "on `localhost`";
   ASCII diagram `[Play 1: proxmox node]` → `[Play 1: localhost]`.

No other files touched. `Out of scope` list (`tasks/network/generate-ip.yml`, Plays 2/3 of the
app template and docker-host playbook) left untouched, confirmed via `git diff` review.

**Gate evidence**

1. `lint` gate — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
   Exit code: 0
   ```
   Passed: 0 failure(s), 0 warning(s) on 62 files. Profile 'min' was required, but 'production' profile passed.
   ```
   Clean pass, no new ansible-lint findings, no delegation/no-changed-when warnings on the
   delegated `pct`/`qm` tasks (they retain `changed_when: false` verbatim).

2. `test` gate — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
   Exit code: 1
   Full output (11 playbooks checked): all four plan-touched playbooks pass syntax-check
   cleanly —
   ```
   == playbooks/apps/_template.yml
   [WARNING]: Could not match supplied host pattern, ignoring: app_deploy
   playbook: playbooks/apps/_template.yml
   == playbooks/docker/create-docker-host.yml
   [WARNING]: Could not match supplied host pattern, ignoring: provisioning
   playbook: playbooks/docker/create-docker-host.yml
   == playbooks/proxmox/create-lxc.yml
   playbook: playbooks/proxmox/create-lxc.yml
   == playbooks/proxmox/create-vm.yml
   playbook: playbooks/proxmox/create-vm.yml
   ```
   (The `[WARNING]: Could not match supplied host pattern` lines are informational —
   `ANSIBLE_INVENTORY=localhost,` neutralizes the dynamic inventory per `test.sh`'s design, so
   Play 2/3 group names like `app_deploy`/`provisioning` don't resolve during a syntax-only
   check. Not a failure.)

   The gate's overall exit code is 1 because three *other*, plan-unrelated playbooks fail:
   `playbooks/maintenance/restart-app.yml` and `playbooks/maintenance/tail-applog.yml`
   (`ERROR! ... 'instance' is undefined` — their play name interpolates `{{ instance }}`
   with no default supplied at syntax-check time) and `playbooks/stacks/rollback-container.yml`
   (`ERROR! Empty playbook, nothing to do` — still a stub per repo TODO list). None of these
   three files are touched by this plan, are not named in its "Affected files" or "Out of
   scope" lists, and are unrelated to the multi-node/`run_once` defect this plan fixes.
   **Confirmed pre-existing**: `git stash`-ing this round's changes and re-running the same
   gate against the prior commit (`fb86ee4`) reproduces the identical three failures with
   identical error text, so this round introduces zero new test-gate failures.

   (decision needed: the plan's Verification section says the `test` gate "--syntax-check
   must pass for every playbook" but only lists `lint` in `## Acceptance criteria`. The three
   failing playbooks are pre-existing stubs (`instance`-undefined play names,
   `rollback-container.yml` empty) outside this plan's scope. I did not fix them — doing so
   would expand scope beyond the plan's eight listed files. Confirm whether the `test` gate
   evidence above (clean for all four touched playbooks, pre-existing failures elsewhere) is
   sufficient, or whether a follow-up plan should stub/fix `restart-app.yml`,
   `tail-applog.yml`, and `rollback-container.yml` so the gate can go fully green.)

Working tree left uncommitted for reviewer diff. `.claude/autobuild/run.json` left in place.

### [reviewer] round 1 — 2026-07-02

[reviewer] verdict: CHANGES

**Gate evidence — verified accurate.** Re-ran both gates myself:
- `lint` gate: exit 0, `Passed: 0 failure(s), 0 warning(s) on 62 files` — matches pasted evidence
  exactly.
- `test` gate: exit 1, identical three failures (`restart-app.yml`, `tail-applog.yml` —
  `'instance' is undefined`; `rollback-container.yml` — empty playbook). I additionally
  `git stash`'d this round's changes myself and reran against HEAD directly — same three
  `ERROR!` lines reproduce verbatim, independently confirming the implementer's pre-existing-bug
  claim. I also ran `--syntax-check` on `_template.yml` and `create-docker-host.yml` in isolation
  (exit 0) to confirm all four plan-touched playbooks not shown adjacent in the full-gate output
  are in fact clean. Gate evidence is accurate and green where it needs to be.

**Diff vs. plan spec — the seven `ansible/` files + `.claude/architecture.md` are correct,
byte-for-byte matching the plan's edit spec.** Verified each of the eight files individually:
`load-user-vars.yml` (three `run_once` removed, `combine(recursive=True)` expression logic
unchanged), `create-lxc.yml`/`create-vm.yml` (hosts/gather_facts retarget only), Play 1 of
`create-docker-host.yml` and `_template.yml` (retarget + five/comment edits only, Plays 2/3
untouched), the three `delegate_to` lines each in `lxc-create.yml`/`vm-create.yml` (exactly the
three `pct`/`qm` wait tasks, API-call and set_fact tasks left undelegated), and
`.claude/architecture.md` (Flows section step-1 + ASCII diagram corrected to `localhost`, seam
text at lines 73-79 untouched). No spec item missed, no in-scope file has an unplanned change.

**Finding — out-of-scope files modified, contradicting the round log's "No other files
touched" claim.** `git diff HEAD --stat` shows four files outside the plan's eight-file list with
real content changes:
- `.claude/meta/006-generate-ip-combine/README.md`, `.claude/meta/100-unattended-upgrades-dedupe/README.md`,
  `.claude/meta/102-restart-tail-assert-order/README.md` — each gained a substantive
  "review 2026-07-02" paragraph describing a *different* defect (an `omit`-placeholder bug in
  `generate-ip.yml`, a `grep -c` package-count bug in `guest-bootstrap.yml`, a notify-on-failure
  bug in `restart-app.yml`). None of this is related to the multinode/`run_once` scoping problem
  this plan fixes, none of these files are in the plan's "Affected files" or "Out of scope" lists,
  and the round log's closing line ("No other files touched... confirmed via git diff review")
  is factually wrong for these three.
- `.claude/settings.local.json` — gained ~15 new `allow` permission entries (wsl/git/PowerShell
  gate-invocation patterns). This one is plausibly harness-managed bookkeeping from tool-approval
  during the round rather than a deliberate content edit, and is lower-severity, but it's still an
  uncommitted diff outside the plan's declared scope that will ride along into any commit of the
  working tree as-is.

I did not revert these — discarding another process's content (the meta README notes look like
genuine, useful dossier-grooming output, not garbage) is a real decision, not trivia, and isn't
mine to make unilaterally. qa needs to decide: strip these four files from the commit (keep them
uncommitted/stash for whatever produced them), fold them into a separate commit, or confirm
they're intentionally bundled — but they should not silently ride into a "decide-multinode-scoping"
commit attributed to this plan's diff.

**On the implementer's (decision needed) — test gate scope:** I agree with the implementer's
read. The plan's `## Acceptance criteria` names only the `lint` gate; the `## Verification`
section's syntax-check language is satisfied for all four plan-touched playbooks (confirmed
independently above). The three failing playbooks are pre-existing, unrelated stubs — reproduced
identically on stashed/pre-round HEAD, so this round introduces zero new test-gate breakage.
Sufficient for this plan; a follow-up meta slice/plan to stub-fix `restart-app.yml`,
`tail-applog.yml`, and `rollback-container.yml` so the gate goes fully green would be reasonable
but is not a blocker here. Final call on record-keeping (e.g. filing that follow-up) belongs to
qa per charter.

**Reflexes:** no secrets, no injection surfaces, no swallowed errors introduced by this diff —
it is `hosts:`/`gather_facts:`/`delegate_to:`/comment changes and `run_once:` removals only.

Verdict is CHANGES solely on the out-of-scope file-touch finding above; the ansible/architecture
diff itself is plan-conformant and gate-clean.

### [qa] round 1 — 2026-07-02

[qa] verdict: PASS

**Reviewer's CHANGES finding — resolved, not an implementer defect.** The four out-of-scope
files (`.claude/meta/006-generate-ip-combine/README.md`, `.claude/meta/100-unattended-upgrades-dedupe/README.md`,
`.claude/meta/102-restart-tail-assert-order/README.md`, `.claude/settings.local.json`) were
already modified in the working tree at session start, **before** implementer round 1 ran — the
session's opening `git status` snapshot lists exactly these four as `M`. They are pre-existing
user/harness changes that the reviewer's `git diff HEAD` picked up, not output of this run. The
implementer's "no other files touched" claim was correct for its own edits. Resolution: **stage
only this plan's files**; the four pre-existing dirty files stay uncommitted in the working tree
for their owner. No implementer round needed — the fix is commit-time staging, which qa owns.

**Implementer's (decision needed) — resolved: current evidence is sufficient.** `## Acceptance
criteria` names only the `lint` gate (exit 0, clean). The `test` gate's three failures
(`maintenance/restart-app.yml`, `maintenance/tail-applog.yml` — `'instance' is undefined` in play
names; `stacks/rollback-container.yml` — empty stub) are pre-existing, reproduced byte-identical
against pre-round HEAD by both implementer and reviewer independently. All four plan-touched
playbooks syntax-check clean. Follow-up: file a backlog item to fix those three stubs so the
test gate can go fully green; not a blocker here.

**Senior pass inspection (per ## Verification checklist):**
- No playbook targets `hosts: proxmox_nodes` anymore (repo-wide grep: only the inventory group
  definition remains). No `run_once` left in `load-user-vars.yml` or Play 1 of
  `create-docker-host.yml`. Remaining `run_once` sits in `generate-ip.yml` (out of scope, meta 006)
  and two maintenance plays that never target `proxmox_nodes`.
- Delegation exact: three `pct` tasks and three `qm` tasks carry
  `delegate_to: "{{ homelabinfra_config.proxmox.node }}"`; the `community.proxmox.proxmox` /
  `proxmox_kvm` API tasks and all `set_fact` builders are undelegated.
- Namespace discipline intact: the only namespace write in the touched files is the unchanged
  `combine(recursive=True)` merge in `load-user-vars.yml` (trailing-whitespace trim only).
- Cross-play handoff unchanged: `add_host` blocks byte-identical apart from a removed `run_once`
  (a no-op — `add_host` bypasses the host loop anyway).
- `.claude/architecture.md` Flows section now names `localhost` for Play 1 and the ASCII diagram
  reads `[Play 1: localhost]`; seam text untouched. The file is untracked and is staged whole with
  this commit — acceptance criterion 1 requires the decision recorded there.
- Follow-up trivia (not fixed, out of the plan's eight files): stale comment at
  `ansible/tasks/stack/find-or-create-host.yml:3` still says "runs on proxmox_nodes".

Gate evidence was independently re-run by the reviewer and matched; no spot-run needed.
Committing: code + `.claude/architecture.md` + this plan file (moved to done/), squashed on
`feat/decide-multinode-scoping`.
