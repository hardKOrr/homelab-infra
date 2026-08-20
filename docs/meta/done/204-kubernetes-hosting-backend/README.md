# 204 — Kubernetes hosting backend

**Status:** done — closed 2026-08-20 after fresh-cluster deploy, backup, clean restore,
single-node-loss, recovery and cleanup acceptance (Rundeck executions 234–253)
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

## Acceptance

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
- [x] Reuse the platform ingress and wiring contracts (2026-08-20). Execution 239 deployed
      Mixpost through Traefik at MetalLB VIP `192.168.0.30`; Caddy served the Foxglove
      hostname over HTTPS, Authentik catalog, Uptime Kuma and OPNsense wiring completed,
      and execution 242 proved a converged re-run. TLS still terminates only at Caddy.
      During the `k3s-3` outage the surviving `k3s-2` MetalLB speaker retained the VIP.
- [x] Prove the pilot's `routing.access: internal` class (2026-08-20). LAN DNS resolved the
      Foxglove hostname to platform Caddy and HTTPS reached Mixpost's own login. `public`
      and `authenticated` remain separate application acceptance concerns; they are not
      hidden acceptance work for this internal pilot.
- [x] Complete status, removal and reclaim behavior (2026-08-20). Lab Status execution 235
      reported retained volumes; executions 236 and 237 proved report and guarded
      destructive delete on an orphan; executions 251 and 252 removed both final test
      instances with `delete_data=true`. Execution 253 then reported all three nodes Ready,
      no application namespaces and no retained PVs, with `changed=0` and `failed=0`.
- [x] Declare the persistent-storage contract: the default StorageClass, physical data
      location, capacity ownership, node-loss behavior, PVC reclaim behavior and the exact
      effect of the removal `delete_data` option. A node-local default must not imply
      failover that the storage layer cannot provide.
      **Defect found and fixed 2026-08-20 (Rundeck executions 230 and 231).** Demoting
      k3s's bundled `local-path` class could not survive a restart of the unit: the deploy
      controller re-stages the packaged addon on every start and marks its own class
      default again. The audit found both classes marked default, the platform's winning
      only by a newer `creationTimestamp` — a coin toss deciding which reclaim policy an
      unqualified PVC got. `local-storage` is now on the node disable list and the role
      owns the provisioner Deployment, RBAC and ConfigMap; `default-local-storage-path` is
      gone, because it only ever substituted into a manifest that is no longer staged.
      Execution 230 converged it live: the `local-storage` addon and its manifest were
      deleted by k3s itself, the bundled class disappeared, and the platform provisioner
      came up in namespace `homelab-storage` registered as `rancher.io/local-path` — the
      name every existing volume records. **One default StorageClass remains, asserted on
      every run.** All four PersistentVolumes survived with their data byte-identical
      (405860 KiB across the four directories on `k3s-3`, storage root still mode 700).
      Execution 231 immediately after reported `changed=0` on all three cluster nodes; the
      six on `localhost` are the documented `vm-clone.yml` baseline.
- [x] Prove application-consistent backup and clean restore (2026-08-20). Execution 243
      dumped MySQL with `--single-transaction` and pushed `database.pxar`, `storage.pxar`
      and `index.json` to PBS snapshot `host/mixpost/2026-08-20T18:13:26Z`. Executions 245
      and 246 proved the no-snapshot list and named-snapshot plan paths without changing
      target data. Execution 249 restored into fresh namespace `app-mixpost-restore`,
      reported database row and storage counts, carried the source `APP_KEY`, and finished
      green. The source's disposable credentials then authenticated against the restored
      application and its profile was visible. The PBS snapshot remains the recovery
      artifact after live application cleanup.
- [x] Prove the declared failure-domain behavior (2026-08-20). VM `168000023` (`k3s-3`)
      was stopped while it held both application storage PVs and web pods. `k3s-1` and
      `k3s-2` remained Ready and the API stayed responsive; the surviving MetalLB speaker
      remained on `k3s-2`. Both routes returned 502. After the five-minute eviction, the
      replacement web pod stayed Pending with `volume node affinity conflict`, rather than
      moving node-local data. Restarting the VM returned all nodes and pods to Ready, and
      the restored login still succeeded.
- [x] Make app application and wiring outcomes observable (2026-08-20). Commit `685a412`
      uses client-side apply output so a converged workload reports no change and makes
      `assert-no-degradations.yml` the play's last task. Execution 242 reported only the
      corrected backup Secret as changed; the workload manifest and existing wiring were
      converged, and no degradation was swallowed.
- [x] Pass both full WSL gates and repeat acceptance from a destroyed-and-rebuilt cluster
      (2026-08-20). Execution 238 recreated all three VMs from scratch before executions
      239–253 exercised deploy, restore, loss, recovery and cleanup. Both gates also passed
      after each restore defect fix found by the live run.

## Links

- `ansible/playbooks/apps/README.md` — current application hosting and wiring contract
- `ansible/tasks/proxmox/vm-clone.yml` — existing reproducible VM seam
- `ansible/tasks/resolve-estate.yml` — estate overlay reused by Kubernetes workloads
- `ansible/tasks/proxmox/record-app-on-guest.yml` — existing ownership-record behavior
- `docs/architecture.md` — platform flow and seam map
- [notes.md](notes.md) — scope boundary, sequencing and deferred niceties
