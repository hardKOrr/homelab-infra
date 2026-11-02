---
name: Hosting on Docker
about: An app runs on Docker on an LXC, or on Docker on an LXC and on a VM
title: "feat(<app>): <App> runs on Docker on an LXC"
---

<!--
Fill in <app> (catalog ID), <App> (display name), <default method> and the dependency
issues. For "on Docker on an LXC and on a VM", use the two-drill lines marked below and
delete the one-drill lines; otherwise delete the two-drill lines. Renumber R and S IDs so
they run R1, R2, … and S1, S2, … with no gaps, and update every set and the Result
line to match.
Requirement order: method selected and default unchanged; method-specific code; gates;
one drill per guest type; DNS, Caddy and Kuma; `hosting.supported`; impossibility.
Public text uses the placeholders in docs/lab-placeholders.md.
-->

**Lab work:** yes. Read `$HOMELAB_LAB_DIR/README.md` for access. Follow `ansible/README.md` → Live operations: Running directly, Fixing and testing a change, and Application recovery drill.

**Target:** instance `<app>`, switched to this method for the drill. Leave every other application in place.
<!-- Two drills: append "For the LXC drill use stack `<app>-lxc` (`guest_type: lxc`); for the VM drill use stack `<app>` (`guest_type: vm`)." -->

**Inputs:** the hosting contract (#569); the Docker-on-LXC host from #570. Shared operations are listed in `ansible/README.md` → Shared operations (PBS client, restore swap, database dump/restore/drop, Kubernetes recovery Jobs). **Login:** the credential Deploy provisions, or the `homelab-infra/apps/<app>-drill-owner` item if its last drill made one. Canaries: those from <App>'s last passing drill issue (search closed `test(<app>)` issues).

<!-- If the issue has a k8s-lessons block, keep it here unchanged. -->

The green lab is disposable: when the lab gets in the way, fix or replace it and continue.

## Requirements

- R1: Where the config selects `hosting.method: docker` on a stack with `guest_type: lxc`, Deploy shall run <App> on Docker on an LXC.
- R2 (two drills only): Where the config selects `hosting.method: docker` on a stack with `guest_type: vm`, Deploy shall run <App> on Docker on a VM.
- R3 (one drill): The drill config shall use a stack named after the app's current stack with `-lxc` appended (`<app>-lxc` for an app with no `stack:`), created with `guest_type: lxc`.
- R3 (two drills): The LXC drill shall use stack `<app>-lxc` with `guest_type: lxc`, and the VM drill shall use stack `<app>` with `guest_type: vm`.
- R4: Where the config does not select Docker on an LXC, Deploy shall run <App> on <default method>, unchanged. (Two drills: "does not select Docker"; take <default method> from the old Outcome's "Its default stays …".)
- R5: The Docker method shall do backup, restore and database dump/drop through the shared operations files, with <App>'s own behavior in their inputs and hooks and no role-local copy (`gate/test-shared-standards.py` fails on one).
- R6: The repository shall pass `bash gate/lint.sh` and `bash gate/test.sh` at the candidate SHA.
- R7: When the recovery drill runs on Docker on an LXC (stack `<app>-lxc`) at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R8 (two drills only): When the recovery drill runs on Docker on a VM (stack `<app>`) at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R9: While <App> runs on Docker, Deploy shall keep exactly one DNS record, one Caddy route and one Kuma monitor for it (SSO as declared), as on the default method.
- R10: When the last drill passes, the app's defaults shall list each combination this issue proves in `hosting.supported` (#569).
- R11: If running <App> on Docker on an LXC (two drills: "or on a VM") is technically impossible, because upstream documents that the app cannot run this way or a device or kernel feature it needs cannot reach the guest, then the worker shall post blocked on the issue with the reason and a link to the upstream source or the exact failing step. Difficulty or effort doesn't count as impossible.

## Requirement sets

<!-- Every R is in exactly one set, except the impossibility R, which is in none. -->

- S1 (code): When R1–R6 are met, the worker shall hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- S2 (execution, two drills only): When R7 is met, the worker shall hand off. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->
- S3 (execution): When R8 (one drill: R7), R9 and R10 are met, the worker shall post done. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->

**Result:** one issue comment: passed, failed, or impossible with the reason (R11); the tested SHA, the Ansible results, the recovery-point reference (placeholders for private parts) and the PR.

<!-- ao-dependencies: #569, #570 -->
