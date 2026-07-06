# fix-generate-ip-instance-clobber

**Type:** fix

**Depends on:** fix-generate-ip-allocation-loop (done 2026-07-04 — the other half of this file)

**Spec:** .claude/specs/namespace-merge-discipline.md; meta slice 006
(`.claude/meta/006-generate-ip-combine/README.md`)

## Goal

Make the `Set network instance facts` task in `ansible/tasks/network/generate-ip.yml:74-85`
merge into `homelabinfra_instance` instead of clobbering it, and stop storing
`default(omit)` placeholders inside the fact — optional network keys must be genuinely absent
when not configured, so downstream `is defined` checks behave correctly.

## Context

`generate-ip.yml` ends with (current lines 74-85, after the allocation-loop fix landed):

```yaml
- name: Set network instance facts
  ansible.builtin.set_fact:
    homelabinfra_instance:
      network:
        name: "{{ network_name }}"
        cidr: "{{ network_config.cidr }}"
        gateway: "{{ network_config.gateway | default(omit) }}"
        dns_servers: "{{ network_config.dns_servers | default(omit) }}"
        bridge: "{{ network_config.bridge | default(omit) }}"
        vlan: "{{ network_config.vlan | default(omit) }}"
        ip_address: "{{ final_ip_address }}"
  run_once: true
```

Two defects, both violations of `.claude/specs/namespace-merge-discipline.md`:

1. **Bare assignment clobbers siblings.** `set_fact: homelabinfra_instance: {network: ...}`
   destroys every other key on `homelabinfra_instance`. Today all callers happen to run
   generate-ip before anything else touches the dict, so it is latent — but
   `tasks/stack/find-or-create-host.yml:84-90` and `tasks/proxmox/lxc-create.yml:21` already
   do `homelabinfra_instance | combine(...)` **without** `default({})` right after generate-ip,
   i.e. they depend on generate-ip having defined the dict. The fix must keep the dict defined
   on every path: use `homelabinfra_instance | default({}) | combine({...}, recursive=True)`.

2. **`default(omit)` inside a fact is an active bug.** Inside a `set_fact` dict literal, `omit`
   becomes a literal `__omit_place_holder__<hex>` string stored in the fact. It passes
   `is defined` and has nonzero `length`, so for any network lacking an explicit
   gateway/bridge/dns_servers the placeholder leaks into rendered Proxmox module args:
   - `tasks/proxmox/lxc-create.yml:41-46` — `gw={{ ...gateway }}` and `bridge={{ ...bridge }}`
     in `lxc_netif` render the placeholder.
   - `tasks/proxmox/lxc-create.yml:62-63` — `dns_servers | length > 0` is true for the
     placeholder string; `nameserver` gets garbage.
   - `tasks/proxmox/vm-create.yml:43-53` (`vm_net0`, `vm_ipconfig0`) and `:66-67`
     (`nameservers`) — same shapes.
   - `vlan` is incidentally saved by the `| int > 0` guard (placeholder casts to 0), but must
     still be built conditionally — no placeholder may remain in the fact.

**Fix shape** (per the spec: "build optional keys conditionally (ternary-combine of `{}`)"):
start from a base dict of the always-present keys (`name`, `cidr`, `ip_address`) and
ternary-combine each optional key, e.g.
`((network_config.gateway is defined) | ternary({'gateway': network_config.gateway}, {}))`.
Parenthesize the `is defined` test before piping to `ternary` — match the parenthesized style
at `lxc-create.yml:62`, not the unparenthesized form at `lxc-create.yml:67`. Keep
`run_once: true` and the task name.

**Optional keys to pass through:** `gateway`, `dns_servers`, `bridge`, `vlan`, and
**`searchdomain`** — in scope by design decision: both consumers already read
`homelabinfra_instance.network.searchdomain` (`lxc-create.yml:66-70` → `searchdomain`,
`vm-create.yml:70-74` → `searchdomains`) but generate-ip never sets it, so those branches are
dead today. Passing it through from `network_config.searchdomain` is the same conditional-key
mechanism and makes the existing consumer code live. No consumer file changes.

**Callers** (interface unchanged: pass `network_name` in, read
`homelabinfra_instance.network.*` out):
- `ansible/playbooks/proxmox/create-lxc.yml:16`
- `ansible/playbooks/proxmox/create-vm.yml:15`
- `ansible/playbooks/docker/create-docker-host.yml:46,65`
- `ansible/tasks/stack/find-or-create-host.yml:65`

**Test path** (no unit harness exists; gates are lint + syntax-check only): a throwaway
value-proof playbook on localhost that (a) pre-sets
`homelabinfra_instance: {sentinel: 'keep-me'}`, (b) defines a minimal
`homelabinfra_config.networks` (one case with all optional keys, one case with only `cidr` —
use `cidr: dhcp` for the minimal case so no inventory/netaddr allocation runs), (c)
`include_tasks` the real `generate-ip.yml`, (d) asserts: `sentinel` still present, no value in
`homelabinfra_instance.network` contains `__omit_place_holder__`, optional keys absent in the
minimal case and present in the full case. Environment facts from the sibling plan
(`.claude/plans/done/fix-generate-ip-allocation-loop.md` Verification, discovered 2026-07-04):
run playbooks with `ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default` (ansible.cfg's `yaml`
callback is broken under community.general 13), venv is `~/.venvs/homelab-ansible` in WSL,
`netaddr` is installed there. The static-allocation path also reads
`groups['proxmox_clients'] | default([])`, which is safe on a bare localhost inventory.

## Acceptance criteria

- `generate-ip.yml` contains no bare `set_fact: homelabinfra_instance:` assignment; the
  network facts land via `homelabinfra_instance | default({}) | combine({...}, recursive=True)`
  (per specs/namespace-merge-discipline.md).
- No `omit` appears anywhere in `generate-ip.yml`'s `set_fact` dicts; optional keys
  (`gateway`, `dns_servers`, `bridge`, `vlan`, `searchdomain`) are present in
  `homelabinfra_instance.network` only when set in the effective `network_config`, and no
  `__omit_place_holder__` string can appear in the fact.
- A caller that pre-populates `homelabinfra_instance.sentinel` retains `sentinel` after
  generate-ip runs (proven by the value-proof playbook).
- `homelabinfra_instance` is defined after generate-ip even when it was undefined before
  (callers at `find-or-create-host.yml:84` and `lxc-create.yml:21` combine without
  `default({})`).
- Only `ansible/tasks/network/generate-ip.yml` changes; no caller or consumer file is touched.
- The `lint` and `test` gates from `.claude/build.yml` pass with no new failures (the three
  pre-existing `test` failures — `restart-app.yml`, `tail-applog.yml`,
  `rollback-container.yml` — are recorded in `.claude/plans/done/fix-generate-ip-allocation-loop.md`).

## Plan

One file changes: `ansible/tasks/network/generate-ip.yml`. Exactly one task is replaced — `Set
network instance facts` (current lines 74-85). Every other task in the file is untouched, and no
caller or consumer file is edited. Work test-first: there is no unit harness, so the "test" is the
two gates plus the mandatory value-proof playbook in Verification.

### Step 1 — Replace the `Set network instance facts` task verbatim

In `ansible/tasks/network/generate-ip.yml`, **replace exactly these current lines 74-85**:

```yaml
- name: Set network instance facts
  ansible.builtin.set_fact:
    homelabinfra_instance:
      network:
        name: "{{ network_name }}"
        cidr: "{{ network_config.cidr }}"
        gateway: "{{ network_config.gateway | default(omit) }}"
        dns_servers: "{{ network_config.dns_servers | default(omit) }}"
        bridge: "{{ network_config.bridge | default(omit) }}"
        vlan: "{{ network_config.vlan | default(omit) }}"
        ip_address: "{{ final_ip_address }}"
  run_once: true
```

with this task **verbatim**:

```yaml
- name: Set network instance facts
  ansible.builtin.set_fact:
    homelabinfra_instance: >-
      {{ homelabinfra_instance | default({}) | combine({
          'network': {
              'name': network_name,
              'cidr': network_config.cidr,
              'ip_address': final_ip_address
            }
            | combine((network_config.gateway is defined) | ternary({'gateway': network_config.gateway}, {}))
            | combine((network_config.dns_servers is defined) | ternary({'dns_servers': network_config.dns_servers}, {}))
            | combine((network_config.bridge is defined) | ternary({'bridge': network_config.bridge}, {}))
            | combine((network_config.vlan is defined) | ternary({'vlan': network_config.vlan}, {}))
            | combine((network_config.searchdomain is defined) | ternary({'searchdomain': network_config.searchdomain}, {}))
        }, recursive=True) }}
  run_once: true
```

Notes for the implementer (do exactly this, no more):

- **Merge, never clobber.** The whole value is
  `homelabinfra_instance | default({}) | combine({'network': ...}, recursive=True)`, so every
  sibling key already on `homelabinfra_instance` (`lxc`, `vm`, `stack`, `vm_ansible_host`, ...) is
  preserved, and the dict is guaranteed defined afterward even if it was undefined on entry
  (Decisions D1, D2). This satisfies the callers at `find-or-create-host.yml:84` and
  `lxc-create.yml:21` that combine **without** `default({})`.
- **Base dict = always-present keys only** (`name`, `cidr`, `ip_address`); each optional key is
  added by a `(<test>) | ternary({...}, {})` combine, so an unset optional key is *genuinely
  absent* from the fact — no `omit`, no `__omit_place_holder__` string anywhere (Decisions D3, D4).
- **JINJA GOTCHA — parenthesize the `is defined` test.** Jinja's `|` filter binds more tightly than
  the `is` test, so an unparenthesized `network_config.gateway is defined | ternary(...)` parses as
  `network_config.gateway is (defined | ternary(...))`, which is wrong. Wrapping the test in parens
  — `(network_config.gateway is defined) | ternary(...)` — forces the boolean to resolve first, then
  pipes it to `ternary`. This matches the correct parenthesized style at `lxc-create.yml:62`, **not**
  the unparenthesized form at `lxc-create.yml:67` (Decision D5). Do not drop a pair of parens.
- **`ternary` is two-arg here.** `(cond) | ternary({'k': v}, {})` — true branch is the single-key
  dict, false branch is `{}` (a no-op combine). `(cond)` is a real boolean, so no third `omit`
  argument is needed (Decision D6).
- **`recursive=True` is on the outer combine only.** The inner `| combine(...)` chain merges flat
  single-key dicts into the base, so recursion is irrelevant there; the outer combine into
  `homelabinfra_instance` must be `recursive=True` to deep-merge `network` alongside existing
  siblings (Decision D7).
- **`searchdomain` passthrough is in scope.** Both consumers already read
  `homelabinfra_instance.network.searchdomain` (`lxc-create.yml:66-70` → `searchdomain`,
  `vm-create.yml:70-74` → `searchdomains`) but generate-ip never set it, so those branches were
  dead. Sourcing it from `network_config.searchdomain` via the same conditional mechanism makes them
  live. No consumer change (Decision D8).
- **`dns_servers` stays a list.** `network_config.dns_servers` is a YAML list; the single-expression
  set_fact preserves the native list type in the fact (as the current code already does). Consumers
  do `| join(' ')` (lxc) / pass the list (vm) — unchanged.
- Keep the task **name** (`Set network instance facts`) and `run_once: true` exactly.

### Step 2 — Confirm no caller or consumer change is needed

Do not edit any other file. Verified against the two consumers:

- `ansible/tasks/proxmox/lxc-create.yml` — `gateway`/`bridge` (`:41,:44`), `vlan is defined and
  (vlan | int) > 0` (`:47`), `dns_servers is defined and (dns_servers | length > 0)` (`:62`), and
  `searchdomain is defined` (`:67`) are all guarded by `is defined`. With optional keys now
  genuinely absent when unset, every guard evaluates correctly and no placeholder can reach a
  rendered module arg.
- `ansible/tasks/proxmox/vm-create.yml` — same guard shapes for `bridge` (`:43`), `vlan` (`:46`),
  `gateway` (`:51`), `dns_servers` (`:66`), `searchdomain` (`:71`). No change needed.

Callers pass `network_name` in and read `homelabinfra_instance.network.*` out; that interface is
unchanged: `playbooks/proxmox/create-lxc.yml:16`, `playbooks/proxmox/create-vm.yml:15`,
`playbooks/docker/create-docker-host.yml:46,65`, `tasks/stack/find-or-create-host.yml:65`.

## Decisions

- **D1 — Wrap with `homelabinfra_instance | default({}) | combine({...}, recursive=True)`.** The bare
  `set_fact: homelabinfra_instance: {network: ...}` destroyed every sibling key (violates
  `specs/namespace-merge-discipline.md`). `combine(recursive=True)` merges the `network` subtree
  while preserving siblings. This is the mandated fix shape and a settled constraint.
- **D2 — Keep `default({})` even though callers combine without it.** `find-or-create-host.yml:84`
  and `lxc-create.yml:21` do `homelabinfra_instance | combine(...)` with no `default({})`, i.e. they
  depend on generate-ip having *defined* the dict. `| default({})` here guarantees it is defined on
  every entry path (undefined-before is a real path for the direct playbook callers), so those
  downstream combines never hit an undefined dict. Required by the Context and acceptance criteria.
- **D3 — Base dict of always-present keys (`name`, `cidr`, `ip_address`); no `omit` anywhere.**
  Storing `default(omit)` inside a `set_fact` dict literal writes the literal
  `__omit_place_holder__<hex>` string into the fact, which passes `is defined` and leaks into
  rendered Proxmox args (`specs/namespace-merge-discipline.md`, Context defect 2). Building only the
  set keys removes the placeholder entirely. Settled constraint.
- **D4 — Add each optional key via `(<test>) | ternary({'k': v}, {})` combine.** This is the spec's
  "build optional keys conditionally (ternary-combine of `{}`)" mechanism: when the key is unset the
  combine merges `{}` (no-op), so the key is absent, not placeholder-valued. Settled constraint.
- **D5 — Parenthesize the `is defined` test.** Jinja's `|` binds tighter than the `is` test;
  `x is defined | ternary(...)` mis-parses as `x is (defined | ternary(...))`. `(x is defined) |
  ternary(...)` resolves the boolean first. Matches `lxc-create.yml:62`, avoids the fragile
  unparenthesized `lxc-create.yml:67` form. Explicit dossier instruction; recorded so nobody
  "simplifies" the parens away.
- **D6 — Two-argument `ternary`, false branch `{}`.** `(x is defined)` is a plain boolean (never
  `None`), so the two-arg `ternary(true_val, false_val)` is sufficient; no third omit-argument. The
  `{}` false branch makes the combine a no-op when the key is unset.
- **D7 — `recursive=True` on the outer combine only.** The inner base-to-optional combines merge
  flat one-key dicts, where recursion has no effect; the outer combine must be recursive to deep-merge
  `network` into `homelabinfra_instance` beside existing siblings. Matches the mandated fix shape.
- **D8 — `searchdomain` is passed through from `network_config.searchdomain`.** In scope by design:
  both consumers already read `homelabinfra_instance.network.searchdomain`, so wiring it makes dead
  consumer branches live via the identical conditional mechanism, with no consumer edit. Settled
  constraint.
- **D9 — Value-proof is two separate plays, each reseeding `homelabinfra_instance: {sentinel:
  'keep-me'}`.** Because the fix uses `combine(recursive=True)`, running the full-optional case and
  then the minimal case against the *same* `homelabinfra_instance` would deep-merge the minimal
  network over the full network and leave stale optional keys — a false failure of "optionals absent
  in the minimal case" that is an artifact of the harness, not the code. Isolating each case in its
  own play (each re-setting the sentinel-only dict before the include) proves both the sibling-key
  retention and the per-case optional-key behavior cleanly. Groomer's call.
- **D10 — Value-proof `include_tasks` uses the absolute path to the real task file.** So the proof
  exercises the shipped `generate-ip.yml` (not a copy) and resolves regardless of where the throwaway
  playbook lives. Groomer's call.

## Verification

**Gates** (the only two defined in `.claude/build.yml`, run from the repo root; read exit codes from
the Bash tool's reported status, not `$?`, per the shell-relay note in `build.yml`):

- **lint** — `wsl bash -lc 'bash .claude/gate/lint.sh'`
  Proves `ansible/tasks/network/generate-ip.yml` parses and lints cleanly under `ansible-lint`
  (`-c .ansible-lint`, profile `min`). No new failure may appear.
- **test** — `wsl bash -lc 'bash .claude/gate/test.sh'`
  Syntax-checks every playbook, including all four callers that import/include this task file
  (`create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml` — and `find-or-create-host.yml` via
  `create-docker-host.yml`, since it is a task file, not a standalone playbook). This gate exits **1**
  because of three pre-existing, unrelated failures — `playbooks/maintenance/restart-app.yml` and
  `tail-applog.yml` (`hosts:` field references undefined `instance`) and
  `playbooks/stacks/rollback-container.yml` (empty playbook) — recorded in
  `.claude/plans/done/fix-generate-ip-allocation-loop.md`. Accept exit 1 only if those three are the
  *only* failures, none touch `generate-ip.yml` or its callers, and `git diff --stat` shows only
  `generate-ip.yml` changed.

**Value proof (mandatory — neither gate executes the fact-merge expression).** Write this throwaway
playbook to the session scratchpad (or `/tmp`) and run it in the gate venv. It pre-seeds a sentinel
sibling key, runs the *real* `generate-ip.yml`, and asserts sentinel retention, no
`__omit_place_holder__` in the fact, and optional keys present-when-set / absent-when-unset:

```yaml
# /tmp/ip-instance-clobber-check.yml — throwaway value-proof for the generate-ip fact merge
- name: FULL case — all optional keys set, sentinel sibling must survive
  hosts: localhost
  gather_facts: false
  vars:
    homelabinfra_config:
      networks:
        full:
          cidr: "192.168.50.0/24"
          gateway: "192.168.50.1"
          dns_servers: ["192.168.50.53", "1.1.1.1"]
          bridge: "vmbr0"
          vlan: "50"
          searchdomain: "lan.example"
  tasks:
    - name: Seed homelabinfra_instance with a sentinel sibling key
      ansible.builtin.set_fact:
        homelabinfra_instance:
          sentinel: "keep-me"

    - name: Run the real generate-ip task file (full network)
      ansible.builtin.include_tasks: /mnt/c/Users/korr/source/repos/homelab-infra/ansible/tasks/network/generate-ip.yml
      vars:
        network_name: "full"

    - name: Prove sentinel kept, all optionals present, no omit placeholder
      ansible.builtin.assert:
        that:
          - homelabinfra_instance.sentinel is defined
          - homelabinfra_instance.sentinel == 'keep-me'
          - homelabinfra_instance.network.name == 'full'
          - homelabinfra_instance.network.cidr == '192.168.50.0/24'
          - homelabinfra_instance.network.gateway is defined
          - homelabinfra_instance.network.dns_servers is defined
          - homelabinfra_instance.network.bridge is defined
          - homelabinfra_instance.network.vlan is defined
          - homelabinfra_instance.network.searchdomain is defined
          - homelabinfra_instance.network.values() | map('string') | select('search', '__omit_place_holder__') | list | length == 0
        fail_msg: "FULL case FAILED: {{ homelabinfra_instance.network }}"
        success_msg: "FULL case OK: {{ homelabinfra_instance.network }}"

- name: MINIMAL case — only cidr set, no optional keys, sentinel sibling must survive
  hosts: localhost
  gather_facts: false
  vars:
    homelabinfra_config:
      networks:
        minimal:
          cidr: "dhcp"
  tasks:
    - name: Reseed homelabinfra_instance with only the sentinel key
      ansible.builtin.set_fact:
        homelabinfra_instance:
          sentinel: "keep-me"

    - name: Run the real generate-ip task file (minimal network)
      ansible.builtin.include_tasks: /mnt/c/Users/korr/source/repos/homelab-infra/ansible/tasks/network/generate-ip.yml
      vars:
        network_name: "minimal"

    - name: Prove sentinel kept, optionals absent, exactly the three base keys
      ansible.builtin.assert:
        that:
          - homelabinfra_instance.sentinel == 'keep-me'
          - homelabinfra_instance.network.name == 'minimal'
          - homelabinfra_instance.network.cidr == 'dhcp'
          - homelabinfra_instance.network.ip_address == 'dhcp'
          - homelabinfra_instance.network.gateway is not defined
          - homelabinfra_instance.network.dns_servers is not defined
          - homelabinfra_instance.network.bridge is not defined
          - homelabinfra_instance.network.vlan is not defined
          - homelabinfra_instance.network.searchdomain is not defined
          - homelabinfra_instance.network.keys() | list | sort == ['cidr', 'ip_address', 'name']
        fail_msg: "MINIMAL case FAILED: {{ homelabinfra_instance.network }}"
        success_msg: "MINIMAL case OK: {{ homelabinfra_instance.network }}"
```

Run:

```
wsl bash -lc 'ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg \
  ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default \
  ~/.venvs/homelab-ansible/bin/ansible-playbook -i localhost, /tmp/ip-instance-clobber-check.yml'
```

Environment prerequisites (from `.claude/plans/done/fix-generate-ip-allocation-loop.md`, discovered
2026-07-04):

- `ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default` is **required**: `ansible/ansible.cfg` sets
  `stdout_callback = yaml` (`community.general.yaml`), removed in community.general 12, which errors
  any real playbook run under the venv's 13.1.0. Do not "fix" ansible.cfg here — that is owned by
  `fix-adhoc-playbook-env`.
- `netaddr` must be importable in `~/.venvs/homelab-ansible` (the FULL case's static allocation uses
  `ansible.utils.ipaddr`). It was installed there on 2026-07-04 (version 1.3.0); if the run fails with
  "Failed to import the required Python library (netaddr)", run
  `wsl bash -lc '~/.venvs/homelab-ansible/bin/pip install netaddr'`.
- The FULL case runs the static-allocation block, which reads `groups['proxmox_clients'] |
  default([])` — safe on a bare `localhost,` inventory (resolves to `[]`, first free host `.1`).

Expected result: **both plays PASS** — two green `assert` tasks, `PLAY RECAP` shows
`failed=0`. Concretely:

- **FULL** → `network = {name: full, cidr: 192.168.50.0/24, ip_address: 192.168.50.1, gateway:
  192.168.50.1, dns_servers: [192.168.50.53, 1.1.1.1], bridge: vmbr0, vlan: 50, searchdomain:
  lan.example}`; `sentinel` still present; no value contains `__omit_place_holder__`.
- **MINIMAL** → `network = {name: minimal, cidr: dhcp, ip_address: dhcp}` and nothing else;
  `sentinel` still present; the five optional keys are absent (`is not defined`).

If either play fails, do not proceed — the diff is not landing correctly. Delete the throwaway file
after the run.

**korr-qa senior pass confirms, from the diff alone:**

- Only `ansible/tasks/network/generate-ip.yml` changed; `git diff --stat` shows exactly one file,
  and only the `Set network instance facts` task differs (all other tasks byte-identical to master).
- No bare `set_fact: homelabinfra_instance:` assignment and no `omit` / `default(omit)` remain
  anywhere in the file; the fact lands via
  `homelabinfra_instance | default({}) | combine({'network': ...}, recursive=True)`.
- The base dict holds only `name`, `cidr`, `ip_address`; each of `gateway`, `dns_servers`, `bridge`,
  `vlan`, `searchdomain` is added via a parenthesized `(network_config.<k> is defined) |
  ternary({'<k>': ...}, {})` combine — no unparenthesized `is defined | ternary`.
- Task name `Set network instance facts` and `run_once: true` are preserved; no caller
  (`create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml`, `find-or-create-host.yml`) or
  consumer (`lxc-create.yml`, `vm-create.yml`) file is touched.
- Gate evidence: lint exit 0 with no new findings; test exit 1 with only the three named pre-existing
  failures. Value-proof output shows both plays green (FULL all optionals present + sentinel kept;
  MINIMAL exactly the three base keys + sentinel kept; no `__omit_place_holder__`).

## Run log

### [implementer] 2026-07-06

Replaced the `Set network instance facts` task in
`ansible/tasks/network/generate-ip.yml` (current lines 74-85 → 74-89) with the verbatim
replacement from Step 1: the bare-clobber `set_fact: homelabinfra_instance: {network: {...}}`
with `default(omit)` placeholders is now
`homelabinfra_instance | default({}) | combine({'network': {...} | combine(...) | combine(...) |
combine(...) | combine(...) | combine(...)}, recursive=True)`, base dict holds only `name`/
`cidr`/`ip_address`, and `gateway`/`dns_servers`/`bridge`/`vlan`/`searchdomain` are each added via
a parenthesized `(network_config.<k> is defined) | ternary({'<k>': ...}, {})` combine. Task name
and `run_once: true` preserved. No other file in the plan's scope touched.

`git diff --stat` shows two files: `ansible/tasks/network/generate-ip.yml` (the plan's change)
and `.claude/settings.local.json`, which was already modified before this round started (present
in the session's initial `git status` as `M .claude/settings.local.json`, pre-existing harness
permission-grant state, not edited by me this round, out of the plan's file scope).

**Files touched:** `ansible/tasks/network/generate-ip.yml` only.

**Gate evidence:**

- `lint` — `wsl bash -lc 'bash .claude/gate/lint.sh'` → exit 0 (no `<error>` from the Bash tool).
  Output: `Passed: 0 failure(s), 0 warning(s) on 64 files. Profile 'min' was required, but
  'production' profile passed.`
- `test` — `wsl bash -lc 'bash .claude/gate/test.sh'` → **Exit code 1** (per the Bash tool's own
  reported status, not `$?` — the shell-relay note in `build.yml` holds: a follow-up
  `wsl bash -lc '...; echo $?'` one-liner silently reported `0` even for a bare `false`, confirming
  `$?` is unreliable across this relay and must not be used). Full captured log (11 playbooks
  found) shows exactly three failures, all pre-existing and unrelated to this change:
  - `playbooks/maintenance/restart-app.yml` — `ERROR! The field 'hosts' has an invalid value...
    'instance' is undefined`
  - `playbooks/maintenance/tail-applog.yml` — same `'instance' is undefined` shape
  - `playbooks/stacks/rollback-container.yml` — `ERROR! Empty playbook, nothing to do`
  All other playbooks passed, including the three direct callers of `generate-ip.yml`:
  `playbooks/proxmox/create-lxc.yml`, `playbooks/proxmox/create-vm.yml`,
  `playbooks/docker/create-docker-host.yml` (which includes `find-or-create-host.yml`), plus
  `playbooks/apps/remove.yml`, `playbooks/apps/_template.yml`, `playbooks/bootstrap.yml`,
  `playbooks/maintenance/check-native-updates.yml`, `playbooks/maintenance/status.yml`.

**Value-proof (mandatory, per Verification):** wrote the plan's throwaway playbook verbatim to
the session scratchpad, ran it with
`MSYS_NO_PATHCONV=1 wsl bash -lc 'ANSIBLE_CONFIG=.../ansible.cfg ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default ~/.venvs/homelab-ansible/bin/ansible-playbook -i localhost, "<scratchpad path>/ip-instance-clobber-check.yml"'`
(the plan's own `wsl bash -lc '...'` invocation needed `MSYS_NO_PATHCONV=1` prepended — without it
the leading `/mnt/c/...` absolute-path arguments were mangled by the Windows-side Git Bash relay,
e.g. `ANSIBLE_CONFIG=/mnt/c/...` became `Files/Git/mnt/c/...`, exit 127; this is the same
shell-relay quoting hazard `build.yml` already documents, worth a note for future rounds).

Both plays passed, `PLAY RECAP`: `ok=18 changed=0 unreachable=0 failed=0 skipped=6 rescued=0
ignored=0`.

- **FULL** → `homelabinfra_instance.network` = `{'name': 'full', 'cidr': '192.168.50.0/24',
  'ip_address': '192.168.50.1', 'gateway': '192.168.50.1', 'dns_servers': ['192.168.50.53',
  '1.1.1.1'], 'bridge': 'vmbr0', 'vlan': '50', 'searchdomain': 'lan.example'}`; `sentinel` still
  `'keep-me'`; no `__omit_place_holder__` in any value. Assert: "FULL case OK".
- **MINIMAL** → `homelabinfra_instance.network` = `{'name': 'minimal', 'cidr': 'dhcp',
  'ip_address': 'dhcp'}` — exactly the three base keys, all five optional keys `is not defined`;
  `sentinel` still `'keep-me'`. Assert: "MINIMAL case OK".

Deleted the throwaway playbook after the run.

No decision needed — all of D1-D10 were followed exactly as specified; no unresolved question
encountered. Working tree left uncommitted on `fix/fix-generate-ip-instance-clobber`, ready for
review.

### [reviewer] 2026-07-06 verdict: PASS

Findings (non-blocking):
- `git diff master -- ansible/tasks/network/generate-ip.yml` matches the plan's Step 1 replacement
  verbatim: bare clobber + `default(omit)` replaced by
  `homelabinfra_instance | default({}) | combine({'network': {...}}, recursive=True)`; base dict
  holds only `name`/`cidr`/`ip_address`; each of `gateway`, `dns_servers`, `bridge`, `vlan`,
  `searchdomain` added via a parenthesized `(network_config.<k> is defined) | ternary({...}, {})`
  combine (grepped — no unparenthesized `is defined | ternary`, no `omit` anywhere in the file).
  Task name and `run_once: true` preserved.
- `git diff master --name-only` shows exactly three paths: the plan file itself, `generate-ip.yml`,
  and `.claude/settings.local.json` (harness permission-grant additions, pre-existing before this
  round per the implementer's note — not a caller/consumer file, not a functional change). No
  caller or consumer file touched.
- Re-ran the `test` gate myself (`wsl bash -lc 'bash .claude/gate/test.sh'`) since it was declared
  red: exit 1, same three pre-existing failures only (`restart-app.yml`, `tail-applog.yml`
  — `'instance' is undefined`; `rollback-container.yml` — empty playbook), `create-lxc.yml` and
  `create-vm.yml` both pass. Matches the logged evidence; did not re-run the green `lint` gate.
- Value-proof log matches the plan's expected FULL/MINIMAL results exactly (network dict contents,
  sentinel retention, absence of `__omit_place_holder__`, `PLAY RECAP failed=0`).
- Gap for design awareness (not blocking this fix, scope was correctly held to the plan's single
  verbatim task replacement): `specs/namespace-merge-discipline.md` also asks that "a task file
  that mutates a namespace documents its inputs and outputs in a header comment" (pattern at
  `tasks/stack/find-or-create-host.yml`). `generate-ip.yml` has no such header, before or after
  this change. Out of this plan's acceptance criteria, so not a fail here, but worth a slice if the
  spec's documentation rule is meant to be enforced repo-wide.

### [qa] 2026-07-06

[qa] verdict: PASS

Senior pass over the diff and run log: the change is exactly the plan's Step 1 verbatim
replacement — bare `set_fact` clobber and all five `default(omit)` placeholders gone, fact lands
via `homelabinfra_instance | default({}) | combine({'network': ...}, recursive=True)`, base dict
holds only `name`/`cidr`/`ip_address`, each optional key (`gateway`, `dns_servers`, `bridge`,
`vlan`, `searchdomain`) added by a parenthesized `(x is defined) | ternary({...}, {})` combine
per D4/D5, task name and `run_once: true` preserved. Only `generate-ip.yml` changed in the
plan's scope; no caller or consumer touched. Gate evidence accepted: lint exit 0 (64 files
clean); test exit 1 with only the three pre-existing failures, independently reproduced by the
reviewer. Value proof executed the real merge expression: FULL case all optionals present +
sentinel kept, MINIMAL case exactly the three base keys + sentinel kept, no
`__omit_place_holder__` anywhere, `failed=0`. The implementer's `MSYS_NO_PATHCONV=1` note and
the reviewer's header-comment gap (spec's inputs/outputs doc rule, pattern at
`find-or-create-host.yml`) are design notes for a future slice — meta 103 is the natural home.
`.claude/settings.local.json` working-tree change is session housekeeping and is deliberately
NOT staged into this commit. Clear to commit.
