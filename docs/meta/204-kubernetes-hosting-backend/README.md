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

Application access and application identity are separate contracts. The hosting backend
must declare whether an application is reachable from the public Internet, only after an
identity check, or only through a named private path. It must continue to use
`routing.identity` to select how Authentik or the application performs authentication.

## Remaining

- [x] Declare the cluster topology and resource contract (2026-08-18). Three k3s servers
      with embedded etcd, one per physical Proxmox node, so the three control-plane VMs
      occupy distinct failure domains and quorum tolerates one node loss. host-1's node is
      2 GB and tainted `NoSchedule` (quorum only) because that node also runs the CARP
      BACKUP firewall peer. Ingress is Traefik behind a MetalLB L2 VIP with
      `--disable=servicelb`. Storage defaults to `local-path`; the database is
      namespace-scoped and in-cluster; the pilot is `mixpost`; backups are
      `proxmox-backup-client` CronJobs into the existing PBS datastore. Sizes, reclaims
      and rationale are in [notes.md](notes.md). k3s version pin and upgrade policy are
      set by the provisioning row below.
- [ ] Provision and re-run the k3s VM cluster idempotently through Ansible. Cluster join
      material and administrative kubeconfig remain credentials. Their canonical values
      must be organization-owned Vaultwarden items after cutover. k3s-managed copies may
      persist only at the protected node paths that k3s requires. Jobs may create additional
      copies only in memory or in mode-restricted temporary runtime files and must remove
      those additional copies when the run ends. Credentials must never enter tracked
      config, generated topology, job output or command-line arguments.
- [ ] Add `kubernetes` as an application hosting kind while retaining the existing
      per-instance config merge, one-job-per-app UI, failure policy and idempotent re-run
      contract. Define deterministic namespace, release and resource ownership so deploy,
      status and removal operate only on resources owned by the selected instance. Helm or
      Kubernetes manifests are implementation details behind that job.
- [ ] Keep application credentials canonical in their organization-owned Vaultwarden
      items. The adapter may create namespace-scoped Kubernetes Secrets as runtime copies,
      but it must redact them from output, update them idempotently when the canonical value
      changes, and remove them with the owned application resources. A Kubernetes Secret is
      not a second canonical secret store.
- [ ] Give the cluster one stable internal ingress address and route the platform Caddy to
      it. Declare how that address is owned or moved when a node fails, and make the
      observed behavior match the declared failure-domain mode. Public TLS terminates at
      the platform Caddy. The k3s API, node addresses, NodePorts and internal ingress must
      remain on named private networks and must not become alternate public entry points.
      Do not move Caddy, Vaultwarden, either Authentik estate, the runner or Proxmox/PBS
      control services into the cluster in this slice.
- [ ] Reuse `routing.estate` and the existing platform wiring behavior for Kubernetes
      workloads: Caddy, the selected Authentik identity mode, Uptime Kuma and DNS must
      produce the same user-visible result as an LXC- or Docker-hosted app.
- [ ] Define one canonical application-config field for the Foxglove access classes and
      keep it distinct from `routing.identity`. Prove the selected pilot class against the
      corresponding behavior:
      - `public`: public DNS resolves, HTTPS succeeds and no platform identity gate blocks
        an anonymous client; the application may still own its own login.
      - `authenticated`: public DNS resolves, HTTPS succeeds, an anonymous client is
        denied or redirected, and an authorized client reaches the application through the
        declared `routing.identity` mode.
      - `private`: no public DNS record or WAN route publishes the application; a client on
        the named private path, such as the LAN or an approved VPN, resolves the hostname
        and reaches it over HTTPS.
      LAN-only DNS is evidence only for the `private` class.
- [ ] Extend status, removal, notifications, registry bookkeeping and app ownership records
      so a Kubernetes workload does not become an untracked second platform. Removal must
      withdraw platform wiring and preserve or delete application data only according to
      an explicit option.
- [ ] Declare the persistent-storage contract: the default StorageClass, physical data
      location, capacity ownership, node-loss behavior, PVC reclaim behavior and the exact
      effect of the removal `delete_data` option. A node-local default must not imply
      failover that the storage layer cannot provide.
- [ ] Define an application-consistent backup contract for databases and persistent
      volumes, including quiesce or dump mechanism, schedule, retention, encryption,
      destination, restore owner and restore procedure. The destination must be PBS-backed
      or explicitly declared. Prove a clean restore of the Foxglove pilot into a fresh
      application namespace and verify application data after restore; VM snapshots alone
      do not satisfy this criterion.
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
