# 204 — Kubernetes hosting backend

**Status:** open
**Subject:** Kubernetes hosting backend
**Related:** 008 (estate contract), 203 (guest app record), 300/302/303/304 (platform wiring)

## Goal

Add k3s as a supported hosting backend for suitable applications without replacing the
working native-LXC and Docker-stack paths. Ansible provisions the cluster on Proxmox VMs;
Rundeck and Semaphore retain one job per app; the platform Caddy remains the external edge;
and the existing estate-aware Caddy, Authentik, Uptime Kuma and DNS contracts publish each
application. The first end-to-end workload is a low-risk Foxglove business application.
Existing stable applications do not migrate as part of this slice.

The backend must make the cluster reproducible and the first workload recoverable. A green
deployment without a proven restore is not acceptance for business data.

## Remaining

- [ ] Declare the cluster topology and resource contract: Proxmox placement, VM sizing,
      addresses, failure domains, k3s version, upgrade policy and stable ingress endpoint.
      Three control-plane VMs only count as highly available when they occupy distinct
      physical failure domains; otherwise the UI and documentation must describe the
      actual single-failure-domain mode.
- [ ] Provision and re-run the k3s VM cluster idempotently through Ansible. Cluster join
      material and administrative kubeconfig remain credentials: canonical values live in
      Vaultwarden/control-plane secret storage and never in tracked or generated topology
      files.
- [ ] Add `kubernetes` as an application hosting kind while retaining the existing
      per-instance config merge, one-job-per-app UI, failure policy and idempotent re-run
      contract. Helm or Kubernetes manifests are implementation details behind that job.
- [ ] Give the cluster one stable internal ingress address and route the platform Caddy to
      it. Do not move Caddy, Vaultwarden, either Authentik estate, the runner or Proxmox/PBS
      control services into the cluster in this slice.
- [ ] Reuse `routing.estate` and the existing platform wiring behavior for Kubernetes
      workloads: Caddy, the selected Authentik identity mode, Uptime Kuma and DNS must
      produce the same user-visible result as an LXC- or Docker-hosted app.
- [ ] Define the Foxglove access classes (`public`, `authenticated`, `private`) and select
      the external path for each class. From a client outside the lab, the pilot hostname
      must resolve, negotiate HTTPS and enforce its declared access class; LAN-only DNS is
      not evidence for this criterion.
- [ ] Extend status, removal, notifications, registry bookkeeping and app ownership records
      so a Kubernetes workload does not become an untracked second platform. Removal must
      withdraw platform wiring and preserve or delete application data only according to
      an explicit option.
- [ ] Define an application-consistent backup contract for databases and persistent
      volumes, with a PBS-backed or otherwise declared destination. Prove a clean restore
      of the Foxglove pilot; VM snapshots alone do not satisfy this criterion.
- [ ] Deploy one low-risk Foxglove business application end to end, re-run it with no
      unwanted change, restart or lose one cluster node, and confirm the result matches the
      declared failure-domain behavior.
- [ ] Both repository gates pass under WSL, followed by live acceptance from a fresh
      cluster path rather than only a previously converged cluster.

## Links

- `ansible/playbooks/apps/README.md` — current application hosting and wiring contract
- `ansible/tasks/proxmox/vm-clone.yml` — existing reproducible VM seam
- `ansible/tasks/resolve-estate.yml` — estate overlay reused by Kubernetes workloads
- `ansible/tasks/proxmox/record-app-on-guest.yml` — existing ownership-record behavior
- `docs/architecture.md` — platform flow and seam map
- [notes.md](notes.md) — scope boundary, sequencing and deferred niceties
