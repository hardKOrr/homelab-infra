---
name: Hosting natively on an LXC
about: An app runs natively on an LXC and passes its recovery drill that way
title: "feat(<app>): <App> runs natively on an LXC"
---

<!--
Fill in <app> (catalog ID), <App> (display name), <default method> (the old Outcome's
"Its default stays …"), <install> (the upstream project's documented install for a Linux
host without containers: its package repository, or its release binary or archive, with a
link) and <version> (the version the current image uses). Keep any app-specific line from
the old Inputs. Keep the issue's ao-dependencies line unchanged as the last line.
Public text uses the placeholders in docs/lab-placeholders.md.
-->

**Lab work:** yes. Read `$HOMELAB_LAB_DIR/README.md` for access. Follow `ansible/README.md` → Live operations: Running directly, Fixing and testing a change, and Application recovery drill.

**Target:** instance `<app>`, switched to this method for the drill. Leave every other application in place.

**Inputs:** the hosting contract in `ansible/vars/contract/hosting.md`; the shared native guest from `ansible/tasks/proxmox/provision-native-guest.yml`, as `roles/_template-native` uses it. Shared operations are listed in `ansible/README.md` → Shared operations. **Login:** the credential Deploy provisions, or the `homelab-infra/apps/<app>-drill-owner` item if its last drill made one. Canaries: those from <App>'s last passing drill issue (search closed `test(<app>)` issues).

The green lab is disposable: when the lab gets in the way, fix or replace it and continue.

## Requirements

- R1: Where the config selects `hosting.method: native` with `proxmox.guest_type: lxc`, Deploy shall run <App> natively on an LXC.
- R2: Where the config does not select the native method on an LXC, Deploy shall run <App> on <default method>, unchanged.
- R3: The <app> role shall install <App> natively from <install>, pinned to <version>.
- R4: The native method on an LXC shall do backup, restore and database dump/drop through the shared operations files, with <App>'s own behavior in their inputs and hooks and no role-local copy (`gate/test-shared-standards.py` fails on one).
- R5: The repository shall pass `bash gate/lint.sh` and `bash gate/test.sh` at the candidate SHA.
- R6: When the recovery drill runs natively on an LXC at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R7: While <App> runs natively on an LXC, Deploy shall keep exactly one DNS record, one Caddy route and one Kuma monitor for it (SSO as declared), as on the default method.
- R8: When the drill passes, the app's defaults shall list `native-lxc` in `hosting.supported`.
- R9: If running <App> natively on an LXC is technically impossible, because upstream documents that the app cannot run this way or a device or kernel feature it needs cannot reach an LXC, then the worker shall post blocked on the issue with the reason and a link to the upstream source or the exact failing step. Difficulty or effort doesn't count as impossible.

## Requirement sets

- S1 (code): When R1–R5 are met, the worker shall hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- S2 (execution): When R6, R7 and R8 are met, the worker shall post done. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->

**Result:** one issue comment: passed, failed, or impossible with the reason (R9); the tested SHA, the Ansible results, the recovery-point reference (placeholders for private parts) and the PR.
