---
name: Hosting methods
about: An app runs on each hosting method it lacks, one method at a time, and passes its recovery drill on each
title: "feat(<app>): <App> runs on its remaining hosting methods"
---

<!--
Fill in <app> (catalog ID), <App> (display name) and <default method> (the old Outcome's
"Its default stays …"). Keep the issue's ao-dependencies line unchanged as the last line.
Public text uses the placeholders in docs/lab-placeholders.md.

One issue covers every method the app still lacks. Work runs one method at a time, start
to finish (its code set, then its drill sets), before the next method starts. Reorder the
method sections and their sets to choose the order.

Delete each method, guest type or set that doesn't apply: one the app's defaults already
list in `hosting.supported`, or one that is out of scope. Keep the remaining R IDs as they
are; gaps are fine, so nothing is renumbered. The last set left ends with "post done"
instead of "hand off".

Kubernetes: R13 is for an app that mounts NFS media or download shares; R14 is for an app
that needs a GPU or another device (Frigate, Jellyfin, Plex, Emby, Immich, Ollama,
ComfyUI), with <device> naming it. Delete the ones that don't apply.
Native: fill in <install> (the upstream project's documented install for a Linux host
without containers: its package repository, or its release binary or archive, with a
link) and <version> (the version the current image uses). Keep any app-specific line from
the old Inputs.
An app-specific Pass bullet becomes one extra R in the execution set of the method it
concerns, right after that method's drill R.
-->

**Lab work:** yes. Read `$HOMELAB_LAB_DIR/README.md` for access. Follow `ansible/README.md` → Live operations: Running directly, Fixing and testing a change, and Application recovery drill.

**Target:** instance `<app>`, switched to the method under test for each drill. Leave every other application in place.

**Inputs (every method):** the hosting contract in `ansible/vars/contract/hosting.md`. Shared operations are listed in `ansible/README.md` → Shared operations. **Login:** the credential Deploy provisions, or the `homelab-infra/apps/<app>-drill-owner` item if its last drill made one. Canaries: those from <App>'s last passing drill issue (search closed `test(<app>)` issues).

The green lab is disposable: when the lab gets in the way, fix or replace it and continue.

## Requirements for every method

- R1: Where the config does not select a method this issue adds, Deploy shall keep <App> on its default method, <default method>, unchanged.
- R2: Each method shall do backup, restore and database dump/drop through the shared operations files, with <App>'s own behavior in their inputs and hooks and no role-local copy (`gate/test-shared-standards.py` fails on one).
- R3: The repository shall pass `bash gate/lint.sh` and `bash gate/test.sh` at each set's candidate SHA.
- R4: While <App> runs on a method this issue adds, Deploy shall keep exactly one DNS record, one Caddy route and one Kuma monitor for it (SSO as declared), as on the default method.
- R5: If running <App> on a method or guest type is technically impossible, because upstream documents that the app cannot run this way or a device or kernel feature it needs cannot reach that guest or the cluster, then the worker shall post blocked for that method on the issue with the reason and a link to the upstream source or the exact failing step, tick that method's remaining sets with `impossible` after them, and continue with the next method. Difficulty or effort doesn't count as impossible.

## Method 1: Kubernetes

**Inputs:** the shared Kubernetes operations in `ansible/README.md` → Shared operations (`tasks/kubernetes/apply-manifest.yml` for the workload; `tasks/kubernetes/recovery-backup.yml` and `recovery-restore.yml` for the backup CronJob and restore Job, driven by the role's `k8s_recovery`; `tasks/database/kubernetes-recovery-step.yml` for a database), as `roles/hi-events` uses them; firewall access through the cluster-node alias (`ansible/tasks/kubernetes/README.md` → Firewall access).

<!-- k8s-lessons -->
**From the finished Kubernetes drills** (#550 n8n, #551 BookStack, #552 Karakeep, #553 Actual Budget, #554 ntfy, #555 Uptime Kuma, #556 Open WebUI, #558 WordPress):
- Copy the shape of the closest finished role: `roles/karakeep` or `roles/actual-budget` (app volumes only), `roles/wordpress-kubernetes` (database and a one-shot CLI Job), `roles/ntfy` (native default), `roles/uptime-kuma` (settings through the app's API). Each adds `tasks/kubernetes.yml` with `templates/manifest.yaml.j2`, a `restore-kubernetes.yml` that `restore.yml` dispatches to, its `k8s_recovery` inputs, and `gate/test-<app>-kubernetes.py`.
- Recovery points: take this drill's own source backup and check it holds both canaries before Remove; an earlier drill's point may have expired. Back up the fresh instance too and pass it as `pre_restore_point`. Keep-last is 2 per day, so a third same-day backup prunes the oldest: write each point down as you make it.
- Take a read-only Status baseline before the first change and compare it at the end. When another app's row is missing, find the run that removed it, this one or a concurrent drill, and name it. This issue fails only for what its own runs changed.
- Repeat Deploy must report `changed=0` on the first try. A corrective run that still changes something (#554's Secret, #558's data directory) is a convergence defect: fix it in this PR.
- A collision with a second-estate instance (database name or role, host port, hostname; #551) is a defect: fix it in the repository in this PR.
- When Config Doctor fails only on another app, run the target's later steps with `LAB_DOCTOR=0` and say so in the result (#553).

- R10: Where the config selects `hosting.method: kubernetes`, Deploy shall run <App> on Kubernetes.
- R11: The Kubernetes method shall give <App>'s volumes the cluster's default StorageClass, as `roles/hi-events` does.
- R12: The repository shall include `gate/test-<app>-kubernetes.py`.
- R13: The Kubernetes method shall mount <App>'s media and download shares in the pod from the NFS shares it uses today, at the same container paths as its Docker stack, through the shared media-share seam `tasks/kubernetes/prepare-media-volumes.yml` and no role-local copy.
- R14: The Kubernetes method shall give <App>'s pod access to <device> on the node that runs it.
- R15: When the recovery drill runs on Kubernetes at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R16: When the Kubernetes drill passes, the app's defaults shall list `kubernetes` in `hosting.supported`.

## Method 2: Native on an LXC and on a VM

**Inputs:** the shared native guest from `ansible/tasks/proxmox/provision-native-guest.yml`, as `roles/_template-native` uses it.

- R20: The <app> role shall install <App> natively from <install>, pinned to <version>.
- R21: Where the config selects `hosting.method: native` with `proxmox.guest_type: lxc`, Deploy shall run <App> natively on an LXC.
- R22: Where the config selects `hosting.method: native` with `proxmox.guest_type: vm`, Deploy shall run <App> natively on a VM.
- R23: When the recovery drill runs natively on an LXC at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R24: When the native LXC drill passes, the app's defaults shall list `native-lxc` in `hosting.supported`.
- R25: When the recovery drill runs natively on a VM at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R26: When the native VM drill passes, the app's defaults shall list `native-vm` in `hosting.supported`.

## Method 3: Docker on an LXC and on a VM

**Inputs:** stack hosts from `ansible/tasks/stack/find-or-create-host.yml`; follow an existing Docker role such as `roles/n8n`.

- R30: Where the config selects `hosting.method: docker` on a stack with `guest_type: lxc`, Deploy shall run <App> on Docker on an LXC.
- R31: Where the config selects `hosting.method: docker` on a stack with `guest_type: vm`, Deploy shall run <App> on Docker on a VM.
- R32: The LXC drill config shall use a stack named after the app's current stack with `-lxc` appended (`<app>-lxc` for an app with no `stack:`), created with `guest_type: lxc`; the VM drill shall use the app's current stack (`<app>` for an app with no `stack:`) with `guest_type: vm`.
- R33: When the recovery drill runs on Docker on an LXC at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R34: When the Docker LXC drill passes, the app's defaults shall list `docker-lxc` in `hosting.supported`.
- R35: When the recovery drill runs on Docker on a VM at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R36: When the Docker VM drill passes, the app's defaults shall list `docker-vm` in `hosting.supported`.

## Requirement sets

Sets run in order; a method's sets all finish before the next method's first set starts.

- [ ] S1 (code, Kubernetes): When R1–R3 and R10–R14 are met, the worker shall tick S1 and hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- [ ] S2 (execution, Kubernetes): When R4, R15 and R16 are met, the worker shall tick S2 and hand off. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->
- [ ] S3 (code, native): When R1–R3 and R20–R22 are met, the worker shall tick S3 and hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- [ ] S4 (execution, native LXC): When R4, R23 and R24 are met, the worker shall tick S4 and hand off. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->
- [ ] S5 (execution, native VM): When R4, R25 and R26 are met, the worker shall tick S5 and hand off. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->
- [ ] S6 (code, Docker): When R1–R3 and R30–R32 are met, the worker shall tick S6 and hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- [ ] S7 (execution, Docker LXC): When R4, R33 and R34 are met, the worker shall tick S7 and hand off. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->
- [ ] S8 (execution, Docker VM): When R4, R35 and R36 are met, the worker shall tick S8 and post done. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->

**Result:** one issue comment per method: passed, failed, or impossible with the reason (R5); the tested SHA, the Ansible results, the recovery-point reference (placeholders for private parts) and the PR.
