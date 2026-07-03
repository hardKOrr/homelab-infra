# fix-ip-to-vmid-int-precedence

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/jinja-string-typing.md; review 2026-07-02

## Goal

Fix the operator-precedence bug in the VMID-from-IP derivation so the last octet keeps its
3-digit zero-padding, and deduplicate the copy-pasted VM/LXC blocks in
`ansible/tasks/proxmox/ip-to-vmid.yml`.

## Context

The documented scheme is `<prefix-octet><octet3 %03d><octet4 %03d>`. In
`ansible/tasks/proxmox/ip-to-vmid.yml:20` (VM) and `:54` (LXC) the expression is

```
(prefix | string) ~ ('%03d' | format(o3)) ~ ('%03d' | format(o4)) | int
```

`|` binds tighter than `~`, so `| int` casts only the final `format` result, stripping its
zero-padding ("050" becomes 50) before concatenation — 192.168.1.50 yields 16800150 instead of
168001050. Fix: parenthesize the whole concatenation before the cast:
`(((prefix | string) ~ ... ~ ...) | int)` (per specs/jinja-string-typing.md).

The file is two verbatim copies of the same four-task sequence, once for `proxmox.vm.*` and once
for `proxmox.lxc.*` (select IP → derive vmid → assert vmid provided for DHCP). Fold into one
sequence parameterized by a `guest_type` var (values `vm`/`lxc`), or a small include invoked
twice — callers (`playbooks/proxmox/create-lxc.yml:25`, `create-vm.yml:24`,
`playbooks/docker/create-docker-host.yml:59,78`, `tasks/stack/find-or-create-host.yml:77`) all
`import_tasks`/`include_tasks` the file with no vars today, so either keep the no-arg interface
(internal loop over both guest types, guarded by key existence as now) or update every caller in
the same change.

Note: existing VMIDs derived with the buggy formula happen to still be unique per IP, so there is
no migration concern — only new guests are affected.

## Acceptance criteria

- 192.168.1.50 derives VMID 168001050 and 10.0.1.5 derives 10001005 (both octets padded; prefix
  rule: second octet unless 0, else first).
- The whole-expression cast is parenthesized; no `~ ... | int` tail remains in the file.
- VM and LXC paths share one task sequence (no verbatim duplicate blocks), and every existing
  caller still works unchanged or is updated in this same change.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
