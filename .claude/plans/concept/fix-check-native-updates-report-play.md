# fix-check-native-updates-report-play

**Type:** fix

**Depends on:** establish-ansible-gate

**Spec:** .claude/specs/provider-noop-wiring.md (homelabinfra_infra is the only source of service
endpoints); review 2026-07-02

## Goal

Make the weekly update-check notification actually able to fire: load `homelabinfra_infra` in the
report play of `ansible/playbooks/maintenance/check-native-updates.yml`, and fix the target group
name mismatch.

## Context

Two defects in one playbook:

1. The "Report available updates" play (lines 34-68) references
   `homelabinfra_infra.notifications.ntfy_url` at line 54, but its pre_tasks only import
   `load-user-vars.yml` — nothing loads `config/.generated/facts.yml` into `homelabinfra_infra`.
   The Ntfy task is gated `when: _updates_available | length > 0`, so the playbook works only
   when it has nothing to say and errors on undefined var exactly when updates exist. The correct
   pattern exists in `playbooks/maintenance/restart-app.yml:33-36`
   (`include_vars` of `{{ playbook_dir }}/../../../config/.generated/facts.yml` with
   `name: homelabinfra_infra`); mirror it, and match provider-conditional behavior (skip the
   notify as a no-op if notifications provider is `none`/facts file absent, per
   specs/provider-noop-wiring.md).
2. Play 1 targets `hosts: tag_homelab_infra` (line 10), but the tag is `homelab-infra` and the
   inventory keyed_groups config (`ansible/inventory/proxmox.yml:13-16`, prefix `tag`,
   separator `_`) does not convert the hyphen — depending on
   `TRANSFORM_INVALID_GROUP_CHARS` the group is `tag_homelab-infra`. Determine the actual group
   name the plugin emits (the same pattern `tag_<stack>` is relied on by
   `tasks/stack/find-or-create-host.yml:23` with underscore stack names, and
   `playbooks/apps/_template.yml:88` comments — pick the convention that matches real plugin
   output and apply it consistently in this playbook).

Also worth carrying in the same change: `hostvars.values() | selectattr('_update_result', ...)`
(lines 43-50) aggregates across all hosts including unreachable ones — verify it degrades
gracefully when play 1 skipped hosts (`ignore_unreachable: true` is already set).

## Acceptance criteria

- The report play loads `homelabinfra_infra` from `config/.generated/facts.yml` before the Ntfy
  task, and the notify task is a silent no-op when the facts file or notifications provider is
  absent (no undefined-variable failure in either case).
- Play 1's host pattern matches the group name the `community.proxmox` inventory plugin actually
  emits for the `homelab-infra` tag, with a comment stating the verified group name.
- The `lint` gate from `.claude/build.yml` passes on the touched file.

## Plan

<!-- korr-groomer -->

## Decisions

<!-- korr-groomer -->

## Verification

<!-- korr-groomer -->

## Run log
