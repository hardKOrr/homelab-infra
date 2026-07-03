# remove-default-lxc-password

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/secrets-handling.md; review 2026-07-02

## Goal

Eliminate the `password: changeme` default for LXC containers in git-managed defaults — every
container created by a user who didn't override it currently gets a known root password.

## Context

`ansible/vars/homelabinfra-defaults.yml:12` ships `proxmox.lxc.password: changeme` to every
clone of this repo. Containers are also provisioned with the user's SSH public key
(`pubkey`, `tasks/proxmox/lxc-create.yml:28`), so password auth is not needed for the platform to
function. Per specs/secrets-handling.md, no hardcoded credential defaults.

Options for the groomer to weigh: (a) drop the key entirely and let the module create the
container without a password (key-only access — verify the `community.proxmox.proxmox` module
accepts absence), (b) generate a random per-container password at create time with
`lookup('password', ...)` — but then it must be stored somewhere useful (Vaultwarden isn't up at
LXC-create time during bootstrap; writing it anywhere else violates the secrets spec), or
(c) require the user to set it explicitly, assert with a friendly message when both password and
pubkey are absent. The philosophy "defaults cover 80%" favors (a) if the module allows it.
Related: meta slice 003's module-arg allowlist decides which keys reach the module at all —
coordinate if both are in flight.

## Acceptance criteria

- `vars/homelabinfra-defaults.yml` contains no literal password value.
- A default deploy still yields root SSH access via the configured public key.
- If a password path is kept, it is user-supplied or generated-and-stored per
  specs/secrets-handling.md — never a shared literal in git.
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
