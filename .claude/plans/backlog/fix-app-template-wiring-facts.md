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

Facts are host-scoped. Play 1 runs on `proxmox_nodes` and sets `homelabinfra_instance` and
`app_config` there, passing them to the deploy target via `add_host` hostvars
(`tasks/stack/find-or-create-host.yml:38-46,92-99`). Play 3 (`_template.yml:117-163`) runs on
`hosts: localhost` and declares:

- `wiring_upstream_host: "{{ homelabinfra_instance.network.ip_address }}"` (line 126) — undefined
  on localhost; and for Docker apps on an *existing* stack host, `network.ip_address` was never
  set anyway (the existing-host path only sets `homelabinfra_instance.stack.ip_address`,
  find-or-create-host.yml:26-35). The correct upstream for Docker apps is the stack host IP.
- `wiring_upstream_port: "{{ app_config.app.port }}"` (line 127) — `app_config` is also a Play 1
  fact, undefined on localhost.
- `wiring_domain`/`wiring_monitor_url` use `homelabinfra_infra.domain` (lines 128, 130) — Play 3
  never loads `config/.generated/facts.yml`, and the facts.yml shape is owned by meta slice 000's
  contract (which does plan a top-level `domain` key). Load it like Play 2 does
  (`_template.yml:96-100`).

Fix shape (pick one, apply consistently): reference Play 1's facts explicitly via
`hostvars[groups['proxmox_nodes'][0]].homelabinfra_instance...`, or extend the `add_host` calls
to also stash what Play 3 needs (e.g. a `wiring` dict on the deploy host, read back via
`hostvars[groups['app_deploy'][0]]`). The template is the contract every future app playbook is
copied from (per `playbooks/apps/README.md`), so whichever shape lands must work for both the
Docker path (PATH A, stack host) and the commented native-LXC path (PATH B, lines 66-81), and the
PATH B comment block must be updated to match.

## Acceptance criteria

- Every `wiring_*` variable in Play 3 resolves from values reachable in Play 3's scope
  (hostvars reads or loaded files — no bare Play 1 fact names), for both PATH A and PATH B.
- Docker apps wire the stack host's IP as upstream; native apps wire the app LXC's IP.
- Play 3 loads `homelabinfra_infra` from `config/.generated/facts.yml` (tolerating absence, per
  specs/provider-noop-wiring.md) before referencing it.
- The `lint` gate from `.claude/build.yml` passes on the touched file.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
