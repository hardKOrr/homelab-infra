---
name: Hosting on Kubernetes
about: An app runs on Kubernetes and passes its recovery drill that way
title: "feat(<app>): <App> runs on Kubernetes"
---

<!--
Fill in <app> (catalog ID), <App> (display name) and <default method> (the old Outcome's
"Its default stays …"). Keep the issue's ao-dependencies line unchanged as the last line.
Public text uses the placeholders in docs/lab-placeholders.md.

R4 is for an app that mounts NFS media or download shares; R5 is for an app that needs a
GPU or another device (Frigate, Jellyfin, Plex, Emby, Immich, Ollama, ComfyUI), with
<device> naming it. Delete the ones that don't apply, renumber the rest, and update the
set ranges and the Result's impossibility R ID to match.
-->

**Lab work:** yes. Read `$HOMELAB_LAB_DIR/README.md` for access. Follow `ansible/README.md` → Live operations: Running directly, Fixing and testing a change, and Application recovery drill.

**Target:** instance `<app>`, switched to this method for the drill. Leave every other application in place.

**Inputs:** the hosting contract in `ansible/vars/contract/hosting.md`; the shared Kubernetes operations in `ansible/README.md` → Shared operations (`tasks/kubernetes/apply-manifest.yml` for the workload; `tasks/kubernetes/recovery-backup.yml` and `recovery-restore.yml` for the backup CronJob and restore Job, driven by the role's `k8s_recovery`; `tasks/database/kubernetes-recovery-step.yml` for a database), as `roles/hi-events` uses them; firewall access through the cluster-node alias (`ansible/tasks/kubernetes/README.md` → Firewall access). **Login:** the credential Deploy provisions, or the `homelab-infra/apps/<app>-drill-owner` item if its last drill made one. Canaries: those from <App>'s last passing drill issue (search closed `test(<app>)` issues).

<!-- k8s-lessons -->
**From the finished Kubernetes drills** (#550 n8n, #551 BookStack, #552 Karakeep, #553 Actual Budget, #554 ntfy, #555 Uptime Kuma, #556 Open WebUI, #558 WordPress):
- Copy the shape of the closest finished role: `roles/karakeep` or `roles/actual-budget` (app volumes only), `roles/wordpress-kubernetes` (database and a one-shot CLI Job), `roles/ntfy` (native default), `roles/uptime-kuma` (settings through the app's API). Each adds `tasks/kubernetes.yml` with `templates/manifest.yaml.j2`, a `restore-kubernetes.yml` that `restore.yml` dispatches to, its `k8s_recovery` inputs, and `gate/test-<app>-kubernetes.py`.
- Recovery points: take this drill's own source backup and check it holds both canaries before Remove; an earlier drill's point may have expired. Back up the fresh instance too and pass it as `pre_restore_point`. Keep-last is 2 per day, so a third same-day backup prunes the oldest: write each point down as you make it.
- Take a read-only Status baseline before the first change and compare it at the end. When another app's row is missing, find the run that removed it, this one or a concurrent drill, and name it. This issue fails only for what its own runs changed.
- Repeat Deploy must report `changed=0` on the first try. A corrective run that still changes something (#554's Secret, #558's data directory) is a convergence defect: fix it in this PR.
- A collision with a second-estate instance (database name or role, host port, hostname; #551) is a defect: fix it in the repository in this PR.
- When Config Doctor fails only on another app, run the target's later steps with `LAB_DOCTOR=0` and say so in the result (#553).

The green lab is disposable: when the lab gets in the way, fix or replace it and continue.

## Requirements

- R1: Where the config selects `hosting.method: kubernetes`, Deploy shall run <App> on Kubernetes.
- R2: Where the config does not select Kubernetes, Deploy shall run <App> on <default method>, unchanged.
- R3: The Kubernetes method shall give <App>'s volumes the cluster's default StorageClass, as `roles/hi-events` does.
- R4: The Kubernetes method shall mount <App>'s media and download shares in the pod from the NFS shares it uses today, at the same container paths as its Docker stack, through the shared media-share seam `tasks/kubernetes/prepare-media-volumes.yml` and no role-local copy.
- R5: The Kubernetes method shall give <App>'s pod access to <device> on the node that runs it.
- R6: The Kubernetes method shall do backup, restore and database dump/drop through the shared operations files, with <App>'s own behavior in their inputs and hooks and no role-local copy (`gate/test-shared-standards.py` fails on one).
- R7: The repository shall include `gate/test-<app>-kubernetes.py` and pass `bash gate/lint.sh` and `bash gate/test.sh` at the candidate SHA.
- R8: When the recovery drill runs on Kubernetes at the candidate SHA, the drill shall restore both canaries exactly from the recorded recovery point into a fresh instance, and the repeat and final Deploy shall report no changes.
- R9: While <App> runs on Kubernetes, Deploy shall keep exactly one DNS record, one Caddy route and one Kuma monitor for it (SSO as declared), as on the default method.
- R10: When the drill passes, the app's defaults shall list `kubernetes` in `hosting.supported`.
- R11: If running <App> on Kubernetes is technically impossible, because upstream documents that the app cannot run this way or a device or kernel feature it needs cannot reach the cluster, then the worker shall post blocked on the issue with the reason and a link to the upstream source or the exact failing step. Difficulty or effort doesn't count as impossible.

## Requirement sets

- S1 (code): When R1–R7 are met, the worker shall hand off. <!-- ao-set-model: claude-sonnet-5-5/medium --> <!-- ao-set-review: gpt-6.1-sol/low -->
- S2 (execution): When R8, R9 and R10 are met, the worker shall post done. <!-- ao-set-model: gpt-6.1-sol/low --> <!-- ao-set-review: claude-sonnet-5-5/low -->

**Result:** one issue comment: passed, failed, or impossible with the reason (R11); the tested SHA, the Ansible results, the recovery-point reference (placeholders for private parts) and the PR.
