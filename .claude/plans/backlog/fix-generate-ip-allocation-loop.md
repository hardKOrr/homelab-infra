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

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
