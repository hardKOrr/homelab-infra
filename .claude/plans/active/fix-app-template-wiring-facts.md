# fix-app-template-wiring-facts

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/provider-noop-wiring.md (wiring contract variables must come from values
visible in the wiring play's scope); review 2026-07-02

## Goal

Fix Play 3 ("Wire") of `ansible/playbooks/apps/_template.yml` so the `wiring_*` contract
variables resolve: they currently read facts that were set on the Proxmox node's host scope and
are undefined on `localhost`.

## Context

**Premise update (2026-07-06):** the original dossier assumed Play 1 ran on `proxmox_nodes`.
Since commit `c72ce33` (decide-multinode-scoping, done/), Play 1 runs on `hosts: localhost` —
the same host as Play 3 — so facts set in Play 1 (`app_config`, `homelabinfra_instance`, and
`homelabinfra_infra` via `include_vars`) DO persist into Play 3's scope. The "undefined on
localhost" framing is obsolete; the remaining defects are narrower:

1. **`wiring_upstream_host: "{{ homelabinfra_instance.network.ip_address }}"`
   (`_template.yml:127`)** — on PATH A's *existing stack host* branch,
   `find-or-create-host.yml:26-36` only sets `homelabinfra_instance.stack.ip_address`;
   `network.ip_address` is undefined → hard error at wire time. On the *new* stack host branch
   it resolves but only coincidentally (generate-ip set it while provisioning the stack host).
   The semantically correct upstream for Docker apps is the stack host IP; for native apps
   (PATH B) it is the app LXC's own IP.
2. **`homelabinfra_infra` reaches Play 3 only by implicit cross-play fact bleed.** Play 1 loads
   `config/.generated/facts.yml` via `include_vars` with **no** `failed_when: false`
   (`_template.yml:46-49`) — a clean checkout or pre-bootstrap run hard-fails there, violating
   specs/provider-noop-wiring.md (absent facts must degrade to no-op wiring, not error). Play 2
   loads the same file tolerantly (`_template.yml:97-101`); Play 1 and Play 3 should match that
   pattern (Play 3 loading it explicitly in `pre_tasks` rather than relying on Play 1's fact).
   Note `wiring_domain`/`wiring_monitor_url` (`_template.yml:129,131`) deref
   `homelabinfra_infra.domain` — play `vars` are lazy, so with all providers absent/none these
   are never evaluated, but any active provider needs the fact defined.
3. **A uniform explicit read is available.** Both PATH A branches
   (`find-or-create-host.yml:38-46,92-99`) and PATH B's commented `add_host`
   (`_template.yml:75-82`) add the deploy target to the `app_deploy` group with `ansible_host`
   (stack IP for Docker, LXC IP for native) and `app_config` stashed as hostvars. So
   `hostvars[groups['app_deploy'][0]].ansible_host` / `.app_config.app.port` resolve correctly
   for **both** paths with no per-path divergence in Play 3 — the wiring block stays identical
   whichever provisioning path the app author keeps.

The template is the contract every future app playbook is copied from (per
`playbooks/apps/README.md`), so the shape that lands must work for PATH A (both branches) and
the commented PATH B (`_template.yml:56-82`), and any comment text that describes the old
behavior must be updated to match (including the stale "runs on proxmox_nodes" header comment
at `find-or-create-host.yml:3` if touched).

## Acceptance criteria

- Every `wiring_*` variable in Play 3 resolves for both PATH A (including the existing-stack-host
  branch, where `homelabinfra_instance.network.ip_address` is undefined) and PATH B, from values
  explicitly visible in Play 3's scope (hostvars reads or files loaded in Play 3 — no reliance on
  implicit cross-play fact bleed).
- Docker apps wire the stack host's IP as upstream; native apps wire the app LXC's IP.
- Play 3 loads `homelabinfra_infra` from `config/.generated/facts.yml` tolerating absence (per
  specs/provider-noop-wiring.md) before referencing it, and Play 1's load of the same file no
  longer hard-fails when the file is absent.
- The `lint` gate from `.claude/build.yml` passes on the touched file(s).

## Plan

All edits are in `ansible/playbooks/apps/_template.yml` plus one comment-only line in
`ansible/tasks/stack/find-or-create-host.yml`. No logic changes to `find-or-create-host.yml`,
no README change, no spec change. The template already hands the deploy target to the
`app_deploy` group with the correct `ansible_host` on every provisioning path (both PATH A
branches at `find-or-create-host.yml:38-46,92-99`, PATH B's `add_host` at `_template.yml:75-82`),
so the fix is to (a) read `wiring_upstream_*` uniformly from that group and (b) make the
`homelabinfra_infra` load tolerant of absence in Plays 1 and 3.

### Step 1 — `_template.yml` Play 1: make the facts load tolerant

Edit the "Load infrastructure facts" task (currently `_template.yml:46-49`). Add
`failed_when: false` so a clean checkout / pre-bootstrap run does not hard-fail, matching Play 2
(`_template.yml:97-101`). Add a trailing inline comment on the task noting the file is optional.

```yaml
    - name: Load infrastructure facts
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../config/.generated/facts.yml"
        name: homelabinfra_infra
      failed_when: false   # optional — absent on a clean checkout / pre-bootstrap run
```

### Step 2 — `_template.yml` PATH B: clarify the `add_host` comment

In the commented PATH B block, keep the `add_host` exactly as-is (its
`ansible_host: "{{ homelabinfra_instance.network.ip_address }}"` is the app LXC's own IP, which
is the correct wiring upstream for a native app). Insert one clarifying comment line immediately
above the `# - name: Add LXC to deploy group` line:

```yaml
    # # ansible_host here is the app LXC's own IP — Play 3 reads it as the wiring upstream.
    # - name: Add LXC to deploy group
```

(Note the double `# ` — the whole PATH B block is commented, so the new comment is commented too.)

### Step 3 — `_template.yml` Play 3: add a tolerant `pre_tasks` load

Play 3 (`_template.yml:118-120`) currently has no `pre_tasks`. Add one between the `gather_facts:
false` line and the `vars:` block, loading `homelabinfra_infra` explicitly in this play (do not
rely on Play 1's localhost fact persisting across plays):

```yaml
- name: "APP_NAME | Wire"
  hosts: localhost
  gather_facts: false

  pre_tasks:
    # Load infra facts in this play rather than relying on Play 1's localhost fact.
    # Tolerate absence per specs/provider-noop-wiring.md: on a clean checkout / pre-bootstrap
    # run there is no facts.yml, homelabinfra_infra stays undefined, and every provider
    # when-condition below falls through its `| default('none')` to a silent no-op.
    - name: Load infrastructure facts
      ansible.builtin.include_vars:
        file: "{{ playbook_dir }}/../../../config/.generated/facts.yml"
        name: homelabinfra_infra
      failed_when: false

  vars:
    ...
```

### Step 4 — `_template.yml` Play 3: fix the wiring contract vars

Replace the `vars:` block body (`_template.yml:122-132`). Change only `wiring_upstream_host` and
`wiring_upstream_port` to read from the `app_deploy` group's hostvars; expand the header comment
to explain why the single read is path-agnostic. All other `wiring_*` lines are unchanged.

```yaml
  vars:
    # ── WIRING CONTRACT ────────────────────────────────────────────────────────
    # These are the variables all wiring tasks read. Set them here.
    # Do not change the variable names — wiring tasks depend on them.
    #
    # upstream host/port come from the deploy target Play 1 registered in the
    # app_deploy group (both PATH A branches and PATH B call add_host). Its
    # ansible_host is the stack host IP for Docker apps and the app LXC IP for
    # native apps, so this one read is correct for every provisioning path — do
    # not special-case per path.
    wiring_app_name: "{{ instance }}"
    wiring_upstream_host: "{{ hostvars[groups['app_deploy'][0]].ansible_host }}"
    wiring_upstream_port: "{{ hostvars[groups['app_deploy'][0]].app_config.app.port }}"
    wiring_domain: "{{ instance }}.{{ homelabinfra_infra.domain }}"
    wiring_app_display: "APP_NAME ({{ instance }})"
    wiring_monitor_url: "https://{{ instance }}.{{ homelabinfra_infra.domain }}"
    wiring_auth_group: "homelab-users"   # Authentik group granted access
```

### Step 5 — `find-or-create-host.yml`: correct the stale header comment

Line 3 currently reads `# Called from Play 1 of Docker app playbooks (runs on proxmox_nodes).`
Change `runs on proxmox_nodes` to `runs on localhost`. Comment-only; no other edit to this file.

## Decisions

- **Contract-var shape (uniform hostvars read vs per-path):** Adopt the uniform
  `hostvars[groups['app_deploy'][0]]` read for `wiring_upstream_host` (`.ansible_host`) and
  `wiring_upstream_port` (`.app_config.app.port`). Why: every provisioning path already registers
  the target in `app_deploy` with the semantically-correct `ansible_host` (stack IP for Docker,
  LXC IP for native), so one read satisfies acceptance for PATH A both branches and PATH B with no
  per-path divergence, and it sources from hostvars rather than localhost fact bleed.
- **Which vars change:** Only `wiring_upstream_host` and `wiring_upstream_port`. Why: `wiring_app_name`
  and `wiring_app_display` use `instance` (an `-e` extra-var, globally visible); `wiring_domain` and
  `wiring_monitor_url` use `homelabinfra_infra.domain` (fixed by Step 3's explicit load);
  `wiring_auth_group` is a literal. Only the two upstream vars read the broken `homelabinfra_instance.network.ip_address`.
- **`wiring_upstream_port` from hostvars, not local `app_config`:** Read `.app_config.app.port` from
  the `app_deploy` hostvars for uniformity, even though `app_config` also exists as a localhost fact.
  Why: acceptance bars reliance on implicit cross-play fact bleed; hostvars-of-the-target is the
  explicit source, and it keeps both upstream vars reading from one place.
- **Play 3 loads facts in pre_tasks, not vars:** Add an `include_vars` in `pre_tasks` (runs before
  `tasks`). Why: matches Play 2's mechanism and the dossier's directive to load explicitly rather
  than inherit Play 1's localhost fact.
- **Tolerant load = `failed_when: false` only (no `default({})` normalize):** Add `failed_when: false`
  to Plays 1 and 3, matching Play 2 verbatim; do not add a follow-up `set_fact` default. Why: Ansible's
  default undefined is chainable (`AnsibleUndefined`/`ChainableUndefined`), so with `homelabinfra_infra`
  left undefined, `homelabinfra_infra.<provider>.provider | default('none')` resolves to `'none'` and
  every wiring `when` is a no-op; `wiring_domain`/`wiring_monitor_url` are lazy play-vars, evaluated
  only when a provider is active (in which case facts.yml exists). [The provider-noop design in the
  existing `when` conditions already depends on this chaining behavior, so the fix introduces no new
  assumption.]
- **`find-or-create-host.yml` stale comment — fix it:** Dossier gated this "in scope only if the file
  is otherwise touched." It is not otherwise touched, but the stale `(runs on proxmox_nodes)` line
  describes the exact premise (Play 1 host) this fix hinges on; leaving it is a landmine that could
  reintroduce the bug. Correct the single comment line; no logic change. Recorded as a deliberate,
  bounded exception.
- **PATH B `add_host` — no logic change, one clarifying comment:** Its `ansible_host` is already the
  app LXC IP, which the new uniform read consumes correctly. Add one comment line making that contract
  explicit for the app author.
- **README.md — no change:** The Wiring Contract Reference table (README lines 119-127) is semantic
  ("App container IP", "App listen port") and remains accurate; the README does not describe the
  fact-loading mechanism or the old `network.ip_address` source, so nothing there is stale. Left
  untouched to keep scope tight.
- **No guard on `groups['app_deploy'][0]`:** Not added. Why: the var is a lazy play-var rendered only
  when a proxy provider is active, and any active-provider run has already run Play 2 against
  `app_deploy`, so the group is non-empty. An empty group implies Play 2 had no target — a broken run
  regardless.

## Verification

- **Gate:** `lint` from `.claude/build.yml` passes on the touched file(s)
  (`ansible/playbooks/apps/_template.yml`, `ansible/tasks/stack/find-or-create-host.yml`). This is the
  only automated gate the acceptance names; the template carries unresolved `APP_NAME` placeholders and
  is not directly runnable, so there is no `test`-gate execution of it.
- **Senior pass confirms by inspection:**
  - `wiring_upstream_host` and `wiring_upstream_port` both read `hostvars[groups['app_deploy'][0]]`
    (`.ansible_host` / `.app_config.app.port`); no residual `homelabinfra_instance.network.ip_address`
    in Play 3.
  - Play 1 and Play 3 both load `config/.generated/facts.yml` with `failed_when: false`; Play 3's load
    is in `pre_tasks`, ahead of the wiring tasks that reference `homelabinfra_infra`.
  - The wiring-contract comment block explains the path-agnostic hostvars source; PATH B has the added
    clarifying comment; `find-or-create-host.yml:3` reads `runs on localhost`.
  - Trace both PATH A branches and PATH B: each populates `app_deploy` with an `ansible_host` that is
    the correct upstream (stack IP / stack IP / LXC IP) and stashes `app_config`, so all seven
    `wiring_*` vars resolve (or lazily no-op when providers are absent).

## Run log
