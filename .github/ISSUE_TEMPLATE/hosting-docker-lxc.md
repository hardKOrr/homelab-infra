---
name: Hosting on Docker on an LXC
about: An app runs on Docker on an LXC and passes its recovery drill that way
title: "feat(<app>): <App> runs on Docker on an LXC"
---

<!--
Fill in <app> (catalog ID), <App> (display name) and <default method> (the old Outcome's
"Its default stays …"). Keep the issue's ao-dependencies line unchanged as the last line.
Public text uses the placeholders in docs/lab-placeholders.md.
An app-specific Pass bullet becomes one extra R right after R6 (the last drill), before
the DNS/Caddy/Kuma R, in the last execution set; renumber the Rs after it.
-->

**Lab work:** yes. Read `$HOMELAB_LAB_DIR/README.md` for access. Follow `ansible/README.md` → Live operations: Running directly, Fixing and testing a change, and Application recovery drill.

**Target:** instance `<app>`, switched to this method for the drill. Leave every other application in place.

**Inputs:** the hosting contract in `ansible/vars/contract/hosting.md`; stack hosts with `guest_type: lxc` from `ansible/tasks/stack/find-or-create-host.yml`. Shared operations are listed in `ansible/README.md` → Shared operations. **Login:** the credential Deploy provisions, or the `homelab-infra/apps/<app>-drill-owner` item if its last drill made one. Canaries: those from <App>'s last passing drill issue (search closed `test(<app>)` issues).

The green lab is disposable: when the lab gets in the way, fix or replace it and continue.

## Requirements

- R1: Where the config selects `hosting.method: docker` on a stack with `guest_type: lxc`, Deploy shall run <App> on Docker on an LXC.
- R2: The drill config shall use a stack named after the app's current stack with `-lxc` appended (`<app>-lxc` for an app with no `stack:`), created with `guest_type: lxc`.
- R3: Where the config does not select Docker on an LXC, Deploy shall keep <App> on its default method, <default method>, unchanged.
- R4: The Docker-on-LXC method shall do backup, restore and database dump/drop through the shared operations files, with <App>'s own behavior in their inputs and hooks and no role-local copy (`gate/test-shared-standards.py` fails on one).
- R5: The repository shall pass `bash gate/lint.sh` and `bash gate/test.sh` at the candidate SHA.
- R6: When the recovery drill runs on Docker on an LXC at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R7: While <App> runs on Docker on an LXC, Deploy shall keep exactly one DNS record, one Caddy route and one Kuma monitor for it (SSO as declared), as on the default method.
- R8: When the drill passes, the app's defaults shall list `docker-lxc` in `hosting.supported`.
- R9: If running <App> on Docker on an LXC is technically impossible, because upstream documents that the app cannot run this way or a device or kernel feature it needs cannot reach an LXC, then the worker shall post blocked on the issue with the reason and a link to the upstream source or the exact failing step. Difficulty or effort doesn't count as impossible.

## Requirement sets

- [ ] S1 (code): When R1–R5 are met, the worker shall tick S1 and hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- [ ] S2 (execution): When R6, R7 and R8 are met, the worker shall tick S2 and post done. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->

**Result:** one issue comment: passed, failed, or impossible with the reason (R9); the tested SHA, the Ansible results, the recovery-point reference (placeholders for private parts) and the PR.
