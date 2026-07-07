# make-stack-host-docker-ready

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/one-click-idempotent.md (one click must yield a running app — no manual
docker install step); review 2026-07-02 (related: meta 103 documents this file's state machine;
meta 006 touches generate-ip which this file calls)

## Goal

Make `ansible/tasks/stack/find-or-create-host.yml` produce a host that can actually run Docker
apps: valid hostname, keyctl/nesting features, and Docker Engine installed on first creation.

## Context

The first Docker app deploy targeting a new stack creates the stack host via
`find-or-create-host.yml`, then Play 2 of `playbooks/apps/_template.yml` goes straight from
`guest-bootstrap` to `community.docker.docker_compose_v2`. Three gaps in the create path
(lines 49-99):

1. **Invalid hostname** — line 58 sets `hostname: stack_name`, but stack names use underscores
   (`media_stack`, matching Proxmox tag/group conventions like `tag_media_stack`), and
   underscores are invalid in hostnames; `pct create` rejects them. Hostname needs
   `stack_name | replace('_', '-')` while the tag (line 59) keeps the underscore form —
   the inventory group `tag_<stack_name>` is how the host is found on subsequent deploys
   (line 23), so the tag must stay verbatim.
2. **Missing container features** — Docker-on-LXC needs keyctl (and nesting, which
   `vars/homelabinfra-defaults.yml:15-16` provides). The keyctl logic already exists in
   `playbooks/docker/create-docker-host.yml:31-44` (handles both mapping and list shapes of
   `proxmox.lxc.features`); it must also run on the find-or-create create path.
3. **Docker never installed** — nothing applies the `docker` role to a freshly created stack
   host. Either find-or-create marks the new host so Play 2 can apply the role conditionally, or
   the app template's Play 2 applies `roles/docker` idempotently before the app role for Docker
   apps. Note find-or-create runs on the proxmox node (Play 1) and cannot itself run the role on
   the guest — the handoff to Play 2 is via `add_host` hostvars (lines 92-99), which is the
   sanctioned cross-play channel (architecture "Cross-play handoff" seam).

The existing-host path (lines 26-46) must stay untouched except as needed for the group/tag
lookup consistency.

## Acceptance criteria

- A created stack host's hostname contains no underscores; its Proxmox tags still include the
  verbatim `stack_name` so `groups['tag_' + stack_name]` finds it on the next deploy.
- The keyctl feature merge (same semantics as `create-docker-host.yml:31-44`) is applied on the
  find-or-create create path.
- After a first-ever deploy of a Docker app to a new stack, Docker Engine is present before the
  app's compose tasks run, without a separate manual job (checkable from the diff: the role
  application path exists and is conditional/idempotent).
- The `lint` gate from `.claude/build.yml` passes on the touched files.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
