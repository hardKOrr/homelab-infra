# strip-secrets-from-guest-instance-json

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/secrets-handling.md; review 2026-07-02

## Goal

Stop writing the Proxmox API token and LXC root password into every provisioned guest: strip auth
keys from the instance JSON that `lxc-create.yml` and `vm-create.yml` persist inside the guest,
and fix the odd `/root/home` destination path.

## Context

`ansible/tasks/proxmox/lxc-create.yml:96-103` pipes `homelabinfra_instance.lxc | to_nice_json`
into the new container at `/root/home/homelabinfra_instance.lxc.json`;
`ansible/tasks/proxmox/vm-create.yml:100-107` does the same via `qm guest exec` for VMs. That
dict is the full module-args dict built at lxc-create.yml:18-29 / vm-create.yml:16-31 and
contains `api_token_secret` (full Proxmox API control) and, for LXC, `password` (root password,
default `changeme` from `vars/homelabinfra-defaults.yml:12`). The file is written with default
umask (world-readable). Per specs/secrets-handling.md, no secret may be persisted into a managed
guest.

Fix shape: filter the dict before dumping — drop at least `api_token_secret`, `api_token_id`,
`password`, `pubkey`/`sshkeys` (e.g. `dict2items | rejectattr('key', 'in', <denylist>) |
items2dict`), write with mode 0600, and settle the destination path (`/root/home/` is almost
certainly a typo for `/root/`; nothing else in the repo reads this file yet — grep to confirm
before choosing). Related: meta slice 003 will replace the dict-splat module call with an
allowlist; this item only sanitizes the persisted copy and should not restructure the module
call.

## Acceptance criteria

- The JSON written into a guest by `lxc-create.yml` and `vm-create.yml` contains no
  `api_token_secret`, `api_token_id`, `password`, or SSH key material (checkable from the diff:
  the denylist/filter is applied in both files).
- The in-guest file lands at a sane path with 0600 permissions.
- No other behavior of the create tasks changes (module call, waits untouched).
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
