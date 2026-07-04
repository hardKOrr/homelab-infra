# fix-generate-ip-allocation-loop

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/jinja-string-typing.md; review 2026-07-02 (related: meta slice 006 covers
the same file's bare set_fact + omit-placeholder bug at lines 75-86 — coordinate, don't collide)

## Goal

Replace the broken static-IP allocation loop in `ansible/tasks/network/generate-ip.yml:33-73`
with a single-expression allocation that cannot hit string-typing failures and fails cleanly when
the subnet is exhausted.

## Context

Jinja native mode is off, so `set_fact` values are strings. The current loop has three defects:

1. `ansible/tasks/network/generate-ip.yml:51-53` sets `candidate_index: "{{ ip_offset - 1 }}"`
   where `ip_offset` is itself a string fact (`"{{ ... | int }}"` at line 48 still stores a
   string) — `"1" - 1` raises a Jinja TypeError on first execution.
2. The `until` at lines 59-61 compares `candidate_index < max_hosts` — string vs string,
   lexicographic (`"10" < "9"` is true), so even with casts the bound check is wrong.
3. The `until` logic is inverted for exhaustion: when the subnet is full the condition stays
   false, `retries` exhaust, and the task itself fails — so the `Assert IP allocation succeeded`
   at lines 65-69 is dead code (`candidate_ip` is always defined after iteration 1).

Used IPs come from `proxmox_client_ips` built at lines 33-44 (hostvars of `proxmox_clients`
group). The allocation should become one expression, shape:

```yaml
candidate_ips: computed from range(ip_offset|int, max_hosts|int) mapped through
               ansible.utils.nthhost over network_config.cidr
final: candidate_ips | reject('in', proxmox_client_ips | default([])) | first | default('')
```

then a real assert that the result is non-empty with the "No available IPs found in <cidr>"
message. The DHCP short-circuit at lines 23-27 and the fact-setting block at lines 75-86 are out
of scope (meta 006 owns lines 75-86). Callers: `playbooks/proxmox/create-lxc.yml`,
`create-vm.yml`, `playbooks/docker/create-docker-host.yml`, `tasks/stack/find-or-create-host.yml`
— all pass `network_name` and read `homelabinfra_instance.network.ip_address` afterward;
interface must not change.

## Acceptance criteria

- No `set_fact`+`until` counter loop remains in `generate-ip.yml`; allocation is a single
  expression with `| int` casts applied inside the expressions that do arithmetic/comparison
  (per specs/jinja-string-typing.md).
- Subnet exhaustion fails via an `assert` with the existing friendly message, not via retries
  exhaustion.
- Occupied IPs (present in `proxmox_client_ips`) are skipped; `ip_offset` and `max_hosts` from
  `network_config` are honored.
- The `lint` gate from `.claude/build.yml` passes on the touched file.

## Plan

One file changes: `ansible/tasks/network/generate-ip.yml`. Only the four defective tasks currently
at lines **51-73** are replaced (Initialize index / Find loop / dead Assert / Set final). The
`Collect existing client IPs` task (33-44, the input that builds `proxmox_client_ips`) and
`Set IP allocation bounds` (46-49, which builds `ip_offset` / `max_hosts`) are **kept unchanged**.
The `Set network instance facts` block (75-86) is **not touched** — it is owned by meta slice 006,
and it already consumes the fact name `final_ip_address`, which this plan preserves (see Decision
D5). Work test-first per the repo gates: there is no unit harness, so the "test" is the syntax-check
gate plus the hand-run value-proof in Verification.

### Step 1 — Delete the broken loop, keep everything above it

In `ansible/tasks/network/generate-ip.yml`, inside the `Allocate static IP in subnet` block
(`when: network_config.cidr is not string or ... != 'dhcp'`), **remove exactly these four tasks**
(current lines 51-73):

```yaml
    - name: Initialize candidate IP index
      ansible.builtin.set_fact:
        candidate_index: "{{ ip_offset - 1 }}"

    - name: Find next available IP in subnet
      ansible.builtin.set_fact:
        candidate_index: "{{ candidate_index + 1 }}"
        candidate_ip: "{{ network_config.cidr | ansible.utils.nthhost(candidate_index + 1) }}"
      until:
        - candidate_ip not in (proxmox_client_ips | default([]))
        - candidate_index < max_hosts
      retries: "{{ max_hosts }}"
      delay: 0

    - name: Assert IP allocation succeeded
      ansible.builtin.assert:
        that:
          - candidate_ip is defined
        fail_msg: "No available IPs found in {{ network_config.cidr }}"

    - name: Set final IP address
      ansible.builtin.set_fact:
        final_ip_address: "{{ candidate_ip }}"
```

Leave the two tasks above them (`Collect existing client IPs`, `Set IP allocation bounds`) exactly
as they are.

### Step 2 — Add the single-expression allocation + a real exhaustion assert

In the place the deleted tasks occupied (still inside the same block, immediately after
`Set IP allocation bounds`), add these two tasks **verbatim**:

```yaml
    - name: Allocate first available static IP in subnet
      ansible.builtin.set_fact:
        final_ip_address: >-
          {{ (range((network_base_int | int) + (ip_offset | int),
                    (network_base_int | int) + (max_hosts | int) + 1)
              | map('ansible.utils.ipaddr')
              | reject('in', proxmox_client_ips | default([]))
              | list | first | default('')) }}
      vars:
        network_address: "{{ (network_config.cidr | ansible.utils.ipaddr('network')).split('/')[0] }}"
        network_octets: "{{ network_address.split('.') }}"
        network_base_int: >-
          {{ (network_octets[0] | int) * 16777216
             + (network_octets[1] | int) * 65536
             + (network_octets[2] | int) * 256
             + (network_octets[3] | int) }}

    - name: Assert an available IP was found in the subnet
      ansible.builtin.assert:
        that:
          - final_ip_address | length > 0
        fail_msg: "No available IPs found in {{ network_config.cidr }}"
```

Notes for the implementer (do exactly this, no more):

- **Why not `map('ansible.utils.nthhost', cidr)`** (the dossier's literal shape): Jinja `map`
  prepends the iterated integer as `nthhost`'s **first** arg (the network position), so it would
  compute `nthhost(index, cidr)` — wrong. `nthhost(value=network, query=index)` is proven by the
  old line 58 (`cidr | nthhost(index)`). The integer-base + `ipaddr` form below is the correct
  single-expression realization of the same intent (Decision D2).
- **`network_base_int` is octet arithmetic on purpose** (Decision D4): `ipaddr('network')` yields
  the bare network address (`192.168.1.0`); `.split('/')[0]` defensively drops any prefix; the four
  octets are folded to the subnet's base integer. This avoids depending on an `ipaddr('int')` query.
- **Cast at point of use** (per `jinja-string-typing.md`): `network_base_int`, `ip_offset`,
  `max_hosts` are all string facts/vars in non-native Jinja, so each is wrapped in `| int` **inside**
  the `range(...)` arithmetic. Do not drop a cast — `"3232235776" + 1` is a TypeError.
- **`map('ansible.utils.ipaddr')`** converts each absolute host integer from the range back to its
  dotted string (`3232235777` → `192.168.1.1`), matching the bare-IP format in `proxmox_client_ips`.
- **`range` upper bound is `+ (max_hosts | int) + 1`** so the candidate indices are
  `ip_offset .. max_hosts` inclusive (for a `/24` with the default `max_hosts = size - 2 = 254`,
  that is `.1 .. .254`, excluding network `.0` and broadcast `.255`).
- **`reject('in', proxmox_client_ips | default([]))`** drops occupied IPs; the `default([])` covers
  an empty/undefined inventory (Decision D7). `first | default('')` yields `''` when the subnet is
  exhausted, which the following assert catches.
- Keep the fact name **`final_ip_address`** — line 85 (`ip_address: "{{ final_ip_address }}"`, out of
  scope) reads it, exactly as the DHCP short-circuit at line 25 already sets it (Decision D5).
- The assert is **inside** the static block, so the DHCP path (which sets `final_ip_address: dhcp`
  and skips this block) never runs it.

### Step 3 — Confirm no caller changes are needed

Do not edit any caller. The interface is unchanged: callers pass `network_name` in and read
`homelabinfra_instance.network.ip_address` out, both untouched:

- `ansible/playbooks/proxmox/create-lxc.yml:16-18` (import_tasks, `network_name`)
- `ansible/playbooks/proxmox/create-vm.yml:15-17` (import_tasks)
- `ansible/playbooks/docker/create-docker-host.yml:46-48` and `:65-67` (import_tasks, LXC + VM)
- `ansible/tasks/stack/find-or-create-host.yml:65-67` (include_tasks)

## Decisions

- **D1 — Replace only the loop (current lines 51-73); keep the input tasks and stay off 75-86.**
  The dossier frames "33-73" but names 33-44 (`Collect existing client IPs`) as the *input* that
  feeds `proxmox_client_ips`, and 46-49 (`Set IP allocation bounds`) is a correct helper. Only
  51-73 (init index / until-loop / dead assert / set final) carry the three defects, so only those
  are removed. Lines 75-86 (`Set network instance facts`) are meta slice 006's territory and are not
  edited — no collision. This is the minimal correct diff and satisfies the acceptance line "no
  `set_fact`+`until` counter loop remains."
- **D2 — Realize the allocation as `range → map('ansible.utils.ipaddr') → reject → first`, NOT
  `map('ansible.utils.nthhost', cidr)`.** The dossier's literal suggestion is subtly wrong: Jinja
  `map` prepends the iterated item as the filter's first positional, and `nthhost(value, query)`
  takes the *network* first (proven by old line 58, `cidr | nthhost(index)`), so
  `range | map('ansible.utils.nthhost', cidr)` computes `nthhost(index, cidr)` — the integer index
  in the network slot. Converting each absolute host-integer with `ipaddr` puts the varying quantity
  in the value slot, where `map` needs it. This is the correct single-expression form the spec's
  `range | map | reject | first` shape calls for.
- **D3 — Preserve today's addressing semantics: `ip_offset = 1` yields the first candidate `.1`.**
  Old code: `candidate_index` initialized to `ip_offset - 1 = 0`, then the first `Find` iteration set
  `candidate_ip = nthhost(candidate_index + 1) = nthhost(1)` = `192.168.1.1` for a `/24`. New code:
  the range starts at `network_base_int + ip_offset`; for `192.168.1.0/24`, base = `3232235776`, so
  the first candidate = `3232235777` → `ipaddr` → `192.168.1.1`. Identical first candidate. The
  mapping is: **candidate for offset N = `ipaddr(network_base_int + N)` = `nthhost(cidr, N)`.**
- **D4 — Compute `network_base_int` by octet arithmetic, not an `ipaddr('int')` query.**
  `ipaddr('network')` (a standard query; `ipaddr('size')` is already relied on at line 49) gives the
  bare network address; `.split('/')[0]` defensively strips any prefix; the four octets fold to the
  base integer via `*16777216 / *65536 / *256`. This keeps the only novel filter dependency to
  `ipaddr` converting an integer back to a dotted string (D-flag below), which is the well-documented
  core behavior of the netaddr-backed `ipaddr` filter.
- **D5 — Set `final_ip_address` directly; do not introduce `candidate_ips` / `final` names.** The
  out-of-scope facts block at line 85 reads `final_ip_address`, and the DHCP branch at line 25
  already writes it. Reusing that exact name keeps the static and DHCP paths symmetric and avoids any
  edit to lines 75-86 (meta 006).
- **D6 — Exhaustion fails via `assert` on `final_ip_address | length > 0` with the existing
  message.** `first | default('')` makes an exhausted subnet resolve to `''`; the assert then
  hard-fails with the verbatim `"No available IPs found in {{ network_config.cidr }}"`. This replaces
  the old inverted behavior where the `until` retries exhausted and the task itself errored before
  the (dead) assert.
- **D7 — `proxmox_client_ips | default([])` in the `reject`.** Empty or undefined inventory (no
  `proxmox_clients`) must reject against an empty list, not raise — matching the dossier requirement
  and the old `until` guard's `default([])`.
- **D8 — No file-level header comment added.** `framework.md` favors per-file headers, but the file
  has none today and adding one is outside this item's stated scope (51-73) and risks touching lines
  near meta 006's region. Left for whichever item owns a broader cleanup.
- **FILTER-FLAG (verify, do not re-decide) —** `map('ansible.utils.ipaddr')` converting an integer
  (e.g. `3232235777`) to a dotted string (`192.168.1.1`) is the documented core behavior of the
  `ansible.utils.ipaddr` filter, but the groomer has no shell to execute it, so it is confirmed by
  the **mandatory value-proof** in Verification rather than by a live run here. If — and only if —
  the value-proof shows `ipaddr` on a bare integer does NOT return a dotted IP, the objective
  contingency is to swap `map('ansible.utils.ipaddr')` for `map('ansible.utils.ipaddr', 'address')`
  (same filter, explicit `address` query) and re-run the proof; if neither yields dotted IPs, stop
  and kick back. This is a verification checkpoint, not an open design choice.
  **RESOLVED 2026-07-04 (korr-design):** the value-proof playbook was executed in the gate venv;
  all three cases matched the expected values exactly (`192.168.1.3`, `10.0.0.1`, `EXHAUSTED`).
  `map('ansible.utils.ipaddr')` on bare integers returns dotted IPs as planned — no contingency
  needed. The implementer still re-runs the proof as evidence, but the design risk is closed.

## Verification

Gates (the only two defined in `.claude/build.yml`); both must be run and their exit codes read from
the Bash tool's reported status (per the shell-relay note in `build.yml`, `$?` does not survive the
outer relay):

- **lint** — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
  Proves the touched file (`ansible/tasks/network/generate-ip.yml`) parses and loads cleanly under
  `ansible-lint` (profile `min`). No new `min`-profile failure may appear. This satisfies the
  acceptance line "the `lint` gate ... passes on the touched file."
- **test** — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
  Syntax-checks every playbook, including all four callers that import/include this task file
  (`create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml`, `find-or-create-host.yml`). Any
  pre-existing failures unrelated to this file (e.g. `restart-app.yml`, `tail-applog.yml`,
  `rollback-container.yml`, as recorded in `.claude/plans/done/fix-ip-to-vmid-int-precedence.md`) are
  accepted only if they reproduce identically on the base branch and none touch `generate-ip.yml` or
  its callers.

**Value proof (mandatory — neither gate executes the allocation expression).** Write a throwaway
playbook that reuses the exact allocation expression and run it in the same venv:

```yaml
# /tmp/ip-alloc-check.yml
- hosts: localhost
  gather_facts: false
  vars:
    cases:
      - { cidr: "192.168.1.0/24", ip_offset: "1", max_hosts: "254", used: ["192.168.1.1", "192.168.1.2"] }
      - { cidr: "10.0.0.0/24",    ip_offset: "1", max_hosts: "254", used: [] }
      - { cidr: "192.168.1.0/30", ip_offset: "1", max_hosts: "2",   used: ["192.168.1.1", "192.168.1.2"] }
  tasks:
    - name: Allocate
      ansible.builtin.debug:
        msg: >-
          {{ item.cidr }} ->
          {{ (range((base | int) + (item.ip_offset | int),
                    (base | int) + (item.max_hosts | int) + 1)
              | map('ansible.utils.ipaddr')
              | reject('in', item.used | default([]))
              | list | first | default('EXHAUSTED')) }}
      vars:
        addr: "{{ (item.cidr | ansible.utils.ipaddr('network')).split('/')[0] }}"
        o: "{{ addr.split('.') }}"
        base: "{{ (o[0]|int)*16777216 + (o[1]|int)*65536 + (o[2]|int)*256 + (o[3]|int) }}"
      loop: "{{ cases }}"
```

Run:

```
wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra/ansible && \
  ANSIBLE_CONFIG=/mnt/c/Users/korr/source/repos/homelab-infra/ansible/ansible.cfg \
  ANSIBLE_INVENTORY=localhost, ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default \
  ~/.venvs/homelab-ansible/bin/ansible-playbook -i localhost, /tmp/ip-alloc-check.yml'
```

Environment prerequisites for the proof run (discovered and handled at design time, 2026-07-04):

- `ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default` is **required**: `ansible/ansible.cfg` sets
  `stdout_callback = yaml` (`community.general.yaml`), which was removed in community.general 12
  and errors out any real playbook run under the venv's 13.1.0. Do not "fix" ansible.cfg in this
  item — the backlog item `fix-adhoc-playbook-env` owns that.
- `netaddr` must be importable in the gate venv (`ansible.utils` IP filters require it). It was
  installed into `~/.venvs/homelab-ansible` on 2026-07-04; if the proof fails with "Failed to
  import the required Python library (netaddr)", run
  `wsl bash -lc '~/.venvs/homelab-ansible/bin/pip install netaddr'`. Adding it to
  `.claude/gate/requirements-dev.txt` is also owned by `fix-adhoc-playbook-env`, not this item.

Expected output, and the hand-computed worked examples the implementer must state in the PR:

- **`192.168.1.0/24`, ip_offset 1, used `[.1, .2]`** → base = `192*16777216 + 168*65536 + 1*256 + 0`
  = **3232235776**; range starts at `3232235776 + 1` = `.1` (used), `.2` (used), `.3` free →
  **`192.168.1.3`**. (Confirms occupied IPs are skipped and ip_offset is honored.)
- **`10.0.0.0/24`, ip_offset 1, used `[]`** → base = `10*16777216` = **167772160**; first candidate
  `167772161` → **`10.0.0.1`**. (Confirms `ip_offset = 1` yields the first host `.1`, i.e. the same
  IP old `nthhost(1)` intended — the addressing-semantics preservation from D3.)
- **`192.168.1.0/30`, ip_offset 1, max_hosts 2, used `[.1, .2]`** → candidate indices 1..2 →
  `.1` (used), `.2` (used) → reject empties the list → `first | default(...)` → empty → the task's
  `assert` fires **`No available IPs found in 192.168.1.0/30`**. (Confirms exhaustion fails via
  `assert`, not via retries exhaustion — the value-proof shows `EXHAUSTED`, the real task shows the
  assert `fail_msg`.)

Delete the throwaway file after. If any `->` value is not a dotted IP (or shows a prefix), apply the
FILTER-FLAG contingency in Decisions and re-run before proceeding.

**korr-qa senior pass confirms, from the diff alone:**

- No `set_fact`+`until` loop and no `candidate_index` / `candidate_ip` remain anywhere in
  `generate-ip.yml`; allocation is the single `range | map | reject | first` expression.
- Every fact-sourced value used in arithmetic/`range` is cast `| int` inline
  (`network_base_int | int`, `ip_offset | int`, `max_hosts | int`) — no bare `string + int`.
- Exhaustion path is an `ansible.builtin.assert` with the verbatim message
  `No available IPs found in {{ network_config.cidr }}`.
- The output fact is `final_ip_address`; lines 75-86 (`Set network instance facts`) and the
  `Collect existing client IPs` / `Set IP allocation bounds` tasks are unchanged; no caller file
  was modified.
- Value proof (`192.168.1.3` / `10.0.0.1` / exhaustion) is stated and matches.

## Run log

### [implementer] 2026-07-04

Implemented Step 1 + Step 2 verbatim. Only file touched: `ansible/tasks/network/generate-ip.yml`
(`git diff --stat`: 1 file changed, 17 insertions(+), 18 deletions(-)). Removed exactly the four
defective tasks at the plan's cited lines 51-73 (`Initialize candidate IP index`, `Find next
available IP in subnet`, `Assert IP allocation succeeded`, `Set final IP address`) and inserted the
two tasks from Step 2 verbatim (`Allocate first available static IP in subnet`,
`Assert an available IP was found in the subnet`). `Collect existing client IPs from proxmox
inventory` and `Set IP allocation bounds` are untouched; `Set network instance facts` (now at
lines 74-85) is untouched; no caller file (`create-lxc.yml`, `create-vm.yml`,
`create-docker-host.yml`, `find-or-create-host.yml`) was edited.

**Gate evidence:**

- `lint` — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/lint.sh'`
  → exit 0. Output: `Passed: 0 failure(s), 0 warning(s) on 63 files. Profile 'min' was required,
  but 'production' profile passed.`
- `test` — `wsl bash -lc 'cd /mnt/c/Users/korr/source/repos/homelab-infra && bash .claude/gate/test.sh'`
  → exit 1 (read from the Bash tool's reported status, not `$?`). `Found 11 playbook(s) to
  syntax-check.` The four callers of this task file all pass syntax-check cleanly: `==
  playbooks/proxmox/create-lxc.yml` → `playbook: playbooks/proxmox/create-lxc.yml`; `==
  playbooks/proxmox/create-vm.yml` → `playbook: playbooks/proxmox/create-vm.yml`; `==
  playbooks/docker/create-docker-host.yml` → `playbook: playbooks/docker/create-docker-host.yml`
  (only a benign `Could not match supplied host pattern, ignoring: provisioning` warning, no
  error). `find-or-create-host.yml` is a task file, not a playbook, so it is not itself
  syntax-checked directly but is exercised only via `create-docker-host.yml`, which passed. The
  three failures present are exactly the pre-existing ones the plan names as acceptable and none
  touch `generate-ip.yml` or its callers: `playbooks/maintenance/restart-app.yml` (`ERROR! The
  field 'hosts' has an invalid value ... 'instance' is undefined`), `playbooks/maintenance/
  tail-applog.yml` (same `instance` undefined error), `playbooks/stacks/rollback-container.yml`
  (`ERROR! Empty playbook, nothing to do`). `git diff --stat` confirms only `generate-ip.yml`
  changed on this branch, so these three are pre-existing and unrelated to this change.

**Value proof (mandatory, per Verification):** wrote the throwaway playbook (byte-identical to
the plan's Verification block) to the session scratchpad and ran it via the exact command given
(substituting the scratchpad path for `/tmp/ip-alloc-check.yml`, with
`ANSIBLE_STDOUT_CALLBACK=ansible.builtin.default` set as required). `netaddr` was already
importable in the gate venv (`~/.venvs/homelab-ansible/bin/python -c "import netaddr;
print(netaddr.__version__)"` → `1.3.0`), so no install step was needed. Output:

```
"192.168.1.0/24 -> 192.168.1.3"
"10.0.0.0/24 -> 10.0.0.1"
"192.168.1.0/30 -> EXHAUSTED"
```

All three match the plan's expected values exactly. Hand-computed worked examples (restated from
the plan, confirmed by the run):

- `192.168.1.0/24`, ip_offset 1, used `[.1, .2]`: base = `192*16777216 + 168*65536 + 1*256 + 0` =
  `3232235776`; range starts at `3232235776 + 1` → `.1` (used), `.2` (used), `.3` free →
  `192.168.1.3`. Confirms occupied IPs are skipped and `ip_offset` is honored.
- `10.0.0.0/24`, ip_offset 1, used `[]`: base = `10*16777216` = `167772160`; first candidate
  `167772161` → `10.0.0.1`. Confirms `ip_offset = 1` yields the first host `.1`, matching the old
  `nthhost(1)` addressing semantics (D3).
- `192.168.1.0/30`, ip_offset 1, max_hosts 2, used `[.1, .2]`: candidate indices 1..2 → `.1`
  (used), `.2` (used) → reject empties the list → `first | default('EXHAUSTED')` → `EXHAUSTED` in
  the proof; in the real task this is `first | default('')` → `''`, which the new
  `Assert an available IP was found in the subnet` catches and fails with the verbatim message
  `No available IPs found in 192.168.1.0/30`. Confirms exhaustion fails via `assert`, not retries
  exhaustion.

Throwaway proof file deleted after the run, per the plan's instruction.

No decisions needed — all of D1-D8 and FILTER-FLAG were already resolved by korr-design; the
change was implemented exactly as specified. No blockers encountered.

### [reviewer] 2026-07-04

[reviewer] verdict: PASS

- Diff (`git diff master -- ansible/tasks/network/generate-ip.yml`) matches the plan's Step 2
  verbatim block exactly; no `set_fact`+`until` counter loop, `candidate_index`, or `candidate_ip`
  remain anywhere in the file.
- All fact-sourced values used in arithmetic/`range` are cast `| int` inline
  (`network_base_int | int`, `ip_offset | int`, `max_hosts | int`).
- Exhaustion path is `ansible.builtin.assert` on `final_ip_address | length > 0` with the verbatim
  message `No available IPs found in {{ network_config.cidr }}`.
- `Collect existing client IPs from proxmox inventory`, `Set IP allocation bounds`, and `Set
  network instance facts` are byte-identical to master; output fact is `final_ip_address`; no
  caller file (`create-lxc.yml`, `create-vm.yml`, `create-docker-host.yml`,
  `find-or-create-host.yml`) was touched.
- Gate evidence: lint was green in the implementer's round (exit 0, 0 failures/warnings on 63
  files) — trusted, not re-run. Test evidence showed exit 1, so per protocol I re-ran it myself:
  reproduces exit 1 with the identical three pre-existing failures (`restart-app.yml`,
  `tail-applog.yml`, `rollback-container.yml` — all `hosts:` field / empty-playbook errors
  unrelated to `generate-ip.yml`); `create-lxc.yml` and `create-vm.yml` both syntax-check clean.
  No new failures introduced.
- Value-proof output (`192.168.1.3` / `10.0.0.1` / `EXHAUSTED`) matches the plan's hand-computed
  expected values exactly.
- Reflexes: no secrets, no injection, no swallowed errors — clean.
- Note (non-blocking): `.claude/settings.local.json` also shows as modified in the working tree
  (adds a `Bash(rm *)` permission-allow entry). This is unrelated harness/session housekeeping, not
  part of this plan's file scope, and not a defect in the reviewed change — flagged for visibility,
  not gating the verdict.

### [qa] 2026-07-04

[qa] verdict: PASS

Senior pass over the diff and run log: the change is exactly the plan's Step 2 block — no
`set_fact`+`until` loop or `candidate_*` facts remain, every arithmetic operand is cast `| int`
inline, exhaustion fails via the assert with the verbatim message, output fact stays
`final_ip_address`, and the surrounding tasks (`Collect existing client IPs`, `Set IP allocation
bounds`, `Set network instance facts`) plus all four callers are byte-identical to master. Gate
evidence accepted: lint exit 0; test exit 1 with only the three pre-existing failures, which the
reviewer independently reproduced. Value proof (`192.168.1.3` / `10.0.0.1` / `EXHAUSTED`) matches
the hand-computed examples. `.claude/settings.local.json` working-tree change is session
housekeeping and is deliberately NOT staged into this commit. Clear to commit.
