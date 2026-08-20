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
- [x] Provision and re-run the k3s VM cluster idempotently through Ansible (2026-08-18,
      Rundeck executions 217 and 218). Three servers Ready, all three `EtcdIsVoter=True`,
      `k3s-1` carrying its quorum-only taint and the other two clean. The re-run reports
      `changed=0` on every cluster node; the six remaining `changed` entries are all inside
      the shared `tasks/proxmox/vm-clone.yml` (`proxmox_kvm` update and `qm resize` both
      self-report changed on a converged guest) — pre-existing, non-mutating, and already
      the case for PBS. Join token and kubeconfig are written to the organization-owned
      Vaultwarden item `homelab-infra/k3s-cluster`; the token travels host to host in
      memory and reaches the installer as an environment variable, never argv; generated
      facts carry topology only and pass the credential-shape check.
- [x] `kubernetes` hosting kind added (2026-08-18), proven live 2026-08-19 (Rundeck
      executions 219-229): the pilot deployed into `app-mixpost`, served, and was
      removed by both `delete_data` paths. Four defects surfaced by that run are
      recorded in [notes.md](notes.md). `hosting:
      kubernetes` in an app's defaults is the only hosting kind that is declared rather
      than inferred — native and Docker are still told apart by the presence of `stack:`.
      The config merge, one-job-per-app UI and wiring contract are unchanged.
      **Ownership is the namespace boundary, not a label selector:** one instance owns
      exactly one namespace `app-<instance>`, so deploy applies into it, status reads it
      and removal deletes it. A label-filtered model over a shared namespace would be one
      forgotten selector away from a removal job deleting a neighbour's database.
      Namespaces are still labelled `app.kubernetes.io/managed-by: homelab-infra`, and a
      namespace without that mark is neither adopted nor deleted — the same rule that
      keeps this platform away from the lab's hand-built guests. Manifests rather than
      Helm, rendered by the app's role. Seams: `tasks/kubernetes/{resolve-cluster,
      ensure-namespace,sync-secret,apply-manifest}.yml`.
- [x] Application credentials stay canonical in Vaultwarden (2026-08-18).
      `tasks/kubernetes/sync-secret.yml` writes a namespace-scoped runtime copy from the
      canonical item and nothing else: every task is `no_log`, the manifest arrives on
      stdin rather than argv, and the Secret is deleted with its namespace. A generated
      credential is stored in Vaultwarden *before* it is applied, so a run that failed in
      between cannot leave a database whose password exists nowhere; a converged re-run
      reads the recorded value back rather than minting a new one, because rotating
      Mixpost's `APP_KEY` would make every OAuth token it holds unreadable.
- [~] Ingress address established (2026-08-18); the Caddy route itself is still to do.
      Traefik holds the declared VIP `192.168.0.30` via MetalLB L2 — `EXTERNAL-IP` matches
      the declaration, the address answers HTTP 404 (Traefik with no matching route, which
      is correct for a cluster carrying no Ingress yet), and ARP resolves it to a live
      speaker. ServiceLB is gone: `kube-system` has no DaemonSets at all. Speakers run on
      `k3s-2` and `k3s-3` only, because `k3s-1`'s taint correctly excludes it, so the VIP
      moves between the two workload nodes. Only `web` (80) is exposed; `websecure` is off,
      so TLS terminates at the platform Caddy alone. Remaining: point the platform Caddy at
      the VIP, and confirm the VIP's observed failover against the declared mode.
- [ ] Reuse `routing.estate` and the existing platform wiring behavior for Kubernetes
      workloads: Caddy, the selected Authentik identity mode, Uptime Kuma and DNS must
      produce the same user-visible result as an LXC- or Docker-hosted app.
- [~] Access classes defined on one canonical field (2026-08-18); the pilot class is not
      yet proven live. The field is the existing `routing.access`, extended from two values
      to three, and it remains distinct from `routing.identity` — access says which network
      path may publish the app, identity says where authentication happens. `internal` is
      the `private` class, `public` is `public`, and the new `authenticated` value is
      publicly routable behind a platform identity check. `tasks/wiring/caddy.yml` already
      separated exposure (the `remote_ip` matcher) from enforcement (the forward_auth
      handler), so the third value needed no new mechanism — only the enum and one assert
      refusing `authenticated` paired with an identity mode that gates nothing, which is
      the combination that publishes an open route while claiming protection.
      The pilot declares `internal`, the class provable on this lab today; `public` and
      `authenticated` are hi-events' job in its own slice. Behaviour still to prove:
      - `public`: public DNS resolves, HTTPS succeeds and no platform identity gate blocks
        an anonymous client; the application may still own its own login.
      - `authenticated`: public DNS resolves, HTTPS succeeds, an anonymous client is
        denied or redirected, and an authorized client reaches the application through the
        declared `routing.identity` mode.
      - `private`: no public DNS record or WAN route publishes the application; a client on
        the named private path, such as the LAN or an approved VPN, resolves the hostname
        and reaches it over HTTPS.
      LAN-only DNS is evidence only for the `private` class.
- [~] Extend status, removal, notifications, registry bookkeeping and app ownership records
      so a Kubernetes workload does not become an untracked second platform. Removal must
      withdraw platform wiring and preserve or delete application data only according to
      an explicit option. Removal is done and proven on both `delete_data` paths
      (2026-08-19), and `maintenance/status.yml` reads the cluster and its managed
      namespaces. Left open: four orphaned PersistentVolumes remain on `k3s-3` from
      those runs — two `Available`, two `Released` with stale claims. Reporting and
      reclaim are now written (2026-08-20): `tasks/kubernetes/list-orphan-volumes.yml`
      feeds a RETAINED VOLUMES section in Lab Status, and `maintenance/reclaim-volume.yml`
      offers `report` (default) / `release` / `delete` on one named volume. Measured live:
      the four hold 396 MiB, all on `k3s-3` under `/var/lib/rancher/k3s/storage/`.
      `unwiring/kubernetes.yml` no longer tells the operator to run `kubectl delete pv`,
      which under Retain strands the bytes. Left open: the destructive `delete` path has
      never been executed, and its storage-root guard was inert until `storage_path` was
      added to the generated facts in the same change — both need the supervised live
      proof.
- [!] Declare the persistent-storage contract: the default StorageClass, physical data
      location, capacity ownership, node-loss behavior, PVC reclaim behavior and the exact
      effect of the removal `delete_data` option. A node-local default must not imply
      failover that the storage layer cannot provide.
      **Known defect, found 2026-08-20:** the demotion of k3s's bundled `local-path`
      class does not survive a restart of the `k3s` unit — the deploy controller
      re-applies the packaged addon manifest and sets `is-default-class` back to
      true. The cluster currently has two default classes; the platform class wins
      only because its `creationTimestamp` is newer. The fix — stop editing the
      bundled addon, and either skip its manifest or run with
      `--disable=local-storage` while owning the provisioner — belongs to this row
      and needs a cluster re-converge. See [notes.md](notes.md).
- [ ] Define an application-consistent backup contract for databases and persistent
      volumes, including quiesce or dump mechanism, schedule, retention, encryption,
      destination, restore owner and restore procedure. The destination must be PBS-backed
      or explicitly declared. Prove a clean restore of the Foxglove pilot into a fresh
      application namespace and verify application data after restore; VM snapshots alone
      do not satisfy this criterion.
      **Code complete 2026-08-20, unproven.** A CronJob dumps MySQL with
      `--single-transaction` and pushes `database.pxar` + `storage.pxar` into the existing
      PBS datastore, pruned to `retention_days`. `Backup App` starts that same CronJob
      rather than defining a second one. `Restore App` is three deliberate steps with
      `overwrite=false` enforced as the default: list snapshots, then verify credentials
      and index, then restore — and a cross-instance restore carries the source `APP_KEY`
      into the target's Vaultwarden item before its Secret, so Laravel-encrypted tokens
      stay readable. Verified only statically: templates render, parse, and pass
      `apply --dry-run=server --server-side` on v1.31.5+k3s1. **No CronJob has ever been
      created and no snapshot exists in PBS**, so the PBS token's datastore permissions
      and the `mysql:8.4` dump flags are both still unexercised. The restore drill is
      Deploy Mixpost → Backup App → Restore App into `mixpost-restore` → sign in with the
      source's credentials.
- [ ] Deploy one low-risk Foxglove business application end to end, re-run it with no
      unwanted change, restart or lose one cluster node, and confirm the result matches the
      declared failure-domain behavior.
- [ ] `tasks/kubernetes/apply-manifest.yml` reports `changed` on every run, so no
      Kubernetes app can ever demonstrate a converged re-run. Its `changed_when` rejects
      lines matching ` unchanged$`, but the apply uses `--server-side`, which prints
      `serverside-applied` unconditionally — verified live on 2026-08-20 against
      v1.31.5+k3s1, where a converged StorageClass printed `serverside-applied` under
      `--server-side` and `configured` without it. Until this is fixed the "re-run with no
      unwanted change" acceptance below cannot be met by observation.
- [ ] `playbooks/apps/mixpost.yml` has neither `report-degradation.yml` nor
      `assert-no-degradations.yml`, though it runs the Caddy, Authentik, Uptime Kuma and
      DNS wiring the platform contract covers. Verified 2026-08-20. Until the assert is its
      last task, any wiring seam that records a degradation there is swallowed and the run
      finishes green — so the backup/restore path deliberately hard-fails instead of
      recording, and that choice should be revisited once the assert exists.
- [ ] Both repository gates pass under WSL, followed by live acceptance from a fresh
      cluster path rather than only a previously converged cluster.

## Links

- `ansible/playbooks/apps/README.md` — current application hosting and wiring contract
- `ansible/tasks/proxmox/vm-clone.yml` — existing reproducible VM seam
- `ansible/tasks/resolve-estate.yml` — estate overlay reused by Kubernetes workloads
- `ansible/tasks/proxmox/record-app-on-guest.yml` — existing ownership-record behavior
- `docs/architecture.md` — platform flow and seam map
- [notes.md](notes.md) — scope boundary, sequencing and deferred niceties
