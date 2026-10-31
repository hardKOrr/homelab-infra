# Gate toolchain

Bootstrap once on Debian/Ubuntu (adjust the package manager for another distro):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/.venvs/homelab-ansible
~/.venvs/homelab-ansible/bin/pip install --upgrade pip
~/.venvs/homelab-ansible/bin/pip install -r gate/requirements-dev.txt
~/.venvs/homelab-ansible/bin/ansible-galaxy collection install -r ansible/requirements.yml
```

Add `~/.venvs/homelab-ansible/bin` to `PATH` so Python scripts called by the platform
can import the installed dependencies.

Run from the repository root:

```bash
bash gate/lint.sh
bash gate/test.sh
```

Both commands always check the applicable tree and accept and ignore arguments.
They close stdin and select a local inventory so no live Proxmox inventory is invoked.

Lint runs `ansible-lint` over `ansible/{playbooks,roles,tasks,vars}`, parses Jinja,
checks repository links and fixture secrets, and enforces GitHub workflow policy.

Test syntax-checks every playbook, then runs every `gate/test-*.py` and `gate/test-*.sh`
suite. Both phases run in parallel and replay each item's diagnostics in order. Set
`GATE_JOBS=n` to choose the concurrency (default: the number of cores), and
`GATE_SHARD=k/n` to run only the k-th of n slices (shard 1 also syntax-checks); CI runs
three shards on separate runners. The suites exercise production logic, expressions and
templates.

- `test-log-brief.py`: `LAB_LOG_BRIEF=1` keeps the findings, failures and result block and drops the TASK banners of silent tasks, a full log keeps every banner, and `rundeck/log-brief.py` applies the same cut to a followed Rundeck log.
- `test-allocate-ip.sh`: address allocation, exclusions and exhaustion.
- `test-generate-ip-vmid.py`: `generate-ip.yml` never reallocates the address an existing guest's VMID encodes, such as an ISO appliance whose config shows no address.
- `test-vmid-from-ip.sh`: Bash/Jinja VMID agreement and valid VMID range.
- `test-comfyui-storage.py`: every directory ComfyUI's Compose file mounts belongs to its runtime user after deploy, and the restore swap hands them back through the same task file.
- `test-instance-guest-reuse.py`: `resolve-instance-guest.yml` reuses the one unclaimed same-name guest Remove kept, refuses ambiguous, wrong-type or claimed guests, and every own-guest playbook selects its guest through it.
- `test-stack-ports.py`: applications that share a Docker stack publish distinct default host ports.
- `test-config.py`: Config Doctor, precedence, estates, mail, public DNS validation, provider gates and networks.
- `test-public-dns-preflight.py`: public address lookup agreement, unsafe address refusal, Cloudflare record ownership preflight, adoption, safe API failures, delayed record marking and its bounded timeout.
- `test-public-dns-status.py`: public resolver matches, drift, missing answers, resolver failure, disabled estates and unchanged output when public DNS is not configured.
- `test-kometa-config.py`: optional operator-owned collection YAML stays in the Kubernetes Secret and mounts read-only only when configured.
- `test-database-facts.py`: generated database registry cleanup, PostgreSQL privilege/extension setup and delete/redeploy cluster lifecycle.
- `test-rundeck-yaml.py`: rendered jobs, YAML aliases, estate options and instance publishing.
- `test-template-rendering.py`: app templates and defaults, including Caddy, the media apps and the Kubernetes manifests.
- `test-dns-rename.py`: DNS-name renames remove only the instance-owned OPNsense override or the former Caddy route hostname in Pi-hole, while preserving foreign and sibling records.
- `test-jellyfin-hardware-transcoding.py`: Jellyfin's VA-API settings and DRI group access follow the declared device, converge on repeat runs, and turn hardware encoding off when no device is declared.
- `test-emby-hardware-transcoding.py`: Emby's VA-API settings and DRI group access follow the declared device, converge on repeat runs, and turn hardware encoding off when no device is declared.
- `test-vaultwarden-login.py`: the vaulted Vaultwarden login round trip and `lab-run.sh` loading it.
- `test-lab-refresh.py`: ordinary runs keep the shared checkout pinned across concurrent jobs and a remote push; explicit refresh still updates it.
- `test-actual-budget-password.py`: Actual Budget's Docker and Kubernetes terminal password exchanges and failure handling.
- `test-actual-budget-kubernetes.py`: Actual Budget's opt-in Kubernetes workload, shared PBS recovery path, and Docker default.
- `test-plex-recovery.py`: Plex native capability/dispatch, non-mutating restore preview, the source-group check and both server-token guards.
- `test-plex-hardware-transcoding.py`: Plex hardware decoding/encoding preferences follow the declared `/dev/dri` device and converge on repeat runs.
- `test-ntfy-recovery.py`: native auth/cache archives and the two paths, owner and auth-database checks Ntfy gives the restore swap.
- `test-influxdb-recovery.py`: native data archive, restore credential checks, and the restore swap inputs with the Bolt and engine staged hook, the operator-token check, and a new target's Vaultwarden credentials written on success and put back on rollback.
- `test-redis-recovery.py`: default native LXC deploy, ACL password rotation, `nopass` convergence and `ACL SAVE` flag preservation, Redis readiness retries, native RDB PBS recovery, the restore swap inputs with the RDB and ACL staged hook, Deploy's file modes and the target-password ACL reconciliation, and scoped guest removal.
- `test-postgresql-recovery.py`: native LXC capability, consistent physical PBS backup, manifest and version guards, the live-cluster check, and the data directory, owner and mode PostgreSQL gives the restore swap.
- `test-uptime-kuma-recovery.py`: quiesced PBS snapshots, destination guards, and the restore swap inputs with the `kuma.db` and initialized-server hooks.
- `test-uptime-kuma-kubernetes.py`: opt-in Kubernetes workload, route and firewall selection, shared setup tasks, and Docker/Kubernetes archive compatibility.
- `test-servarr-recovery.py`: shared four-app dispatch, restore preview, the restore swap inputs and API key continuity: the staged hook refuses a point without the database or key, the replaced hook adopts the archived key in Compose, and the rollback hook puts the original Compose back.
- `test-sabnzbd-clients.py`: authenticated category creation, unique client wiring, convergence and selected-client removal without changing siblings.
- `test-sabnzbd-restore.py`: SABnzbd's restore swap inputs, the staged hook refusing a point without an API key, and the authenticated queue check before the key is published.
- `test-bazarr-restore.py`: Bazarr's restore swap inputs, Deploy and Restore reading the same API key, the staged hook refusing a point without one, and the authenticated settings check before the key is published.
- `test-deemix-restore.py`: ARL continuity, backup inputs and Deemix's restore swap inputs.
- `test-emby-restore.py`: Emby's restore swap inputs and the hook requiring the restored server to be Emby, accept the source account and hold a media library.
- `test-actual-budget-restore.py`: Actual Budget's restore swap inputs and the hook reapplying the server password.
- `test-immich-restore.py`: Immich's restore swap inputs for its media mount and its database and dump.
- `test-forgejo-recovery.py`: Forgejo's quiesced data archive, and its restore swap inputs with the database dump and the staged hook refusing a point without `app.ini`.
- `test-mautic-recovery.py`: Mautic's one-archive backup, its restore swap inputs, the staged hook refusing a point without `local.php` or media, and the replaced hook pointing the restored `local.php` at the target in a real play, with the same PHP escaping as Deploy.
- `test-authentik-recovery.py`: Authentik's quiesced one-archive backup and its restart, and its restore swap inputs: the native PBS client, one path per owner (PostgreSQL and Redis keep their live owner), and the staged hook refusing a point without a PostgreSQL cluster.
- `test-open-webui-deploy.py`: Open WebUI's default Docker bootstrap, Kubernetes PVC/workload/Ingress, cluster firewall inputs, and shared backup wiring.
- `test-open-webui-restore.py`: Open WebUI's Docker restore swap inputs and key hooks, plus Kubernetes recovery Job inputs and the key hook that updates Vaultwarden and the target Secret before startup.
- `test-plane-restore.py`: Plane's restore swap inputs (every data bind mount, the whole project stopped, the routed health check) under its lock, the staged release and RDB checks, and the Redis RDB swapped on the backend guest and put back on rollback with the target's own keys.
- `test-odoo-restore.py`: Odoo's restore swap inputs for its data_dir, database and dump.
- `test-n8n-restore.py`: n8n's Docker restore swap inputs, the replaced hook giving the target the source encryption key in Vaultwarden and `n8n.env`, and rollback giving it back its own.
- `test-jellyfin-recovery.py`: Jellyfin's PBS backup, the pre-restore point requirement, and the restore swap inputs with the single intact `jellyfin.db` staged hook and the configured-server, source-account and library checks.
- `test-vaultwarden-recovery.py`: Vaultwarden's native recovery contract, additional-instance isolation, Remove with an absent database backend, and the restore swap inputs with the `db.sqlite3` staged hook and the web vault check.
- `test-recovery-dispatch.py`: backup and restore inputs remain available across dispatch plays, and LiteLLM, Plane and Odoo Remove guard and delete only their validated, unshared named PostgreSQL state.
- `test-proxmox-task.py`: guest backup/restore task IDs, exact polling paths and invalid-output guards.
- `test-proxmox-ha.py`: opt-in HA resource creation, idempotence, owned-resource removal, shared-storage refusal and Remove ordering.
- `test-guest-placement.py`: database pinning, ordered floating node selection, class storage and placement refusal paths.
- `test-database-recovery.py`: shared database client dump and restore per engine (PostgreSQL, MariaDB, MySQL) against command shims, the ownership guard before drop or restore, the version-matched client image, and Remove dropping every registered-backend database from the controller.
- `test-database-backend-placement.py`: shared backend placement for all five engines, per-instance node/storage defaults, legacy type compatibility, mismatch refusal and single-node LXC/VM placement.
- `test-database-memory.py`: database memory ratios, LXC/VM guest inputs, per-instance overrides and rendered or managed engine settings.
- `test-decommission-retire.py`: PBS stamp retirement, credential provenance checks and repeat execution.
- `test-orphan-stack-tag.py`: the orphan stack tag repair's plan, execute, repeat and refusal cases.
- `test-repair-missing-stack-tag.py`: the missing stack identity repair's plan, execute, repeat and refusal cases.
- `test-start-owned-guest.py`: exact owned stack guest start, readiness, repeat and refusal cases.
- `test-orphan-postgresql-tag.py`: exact owned-guest selection, plan, tag-only repair, repeat and refusal cases.
- `test-qbittorrent-clients.py`: unique client wiring, convergence and selected-client removal.
- `test-plex-restore.py`: Plex's restore swap inputs, the staged hook refusing a point without Preferences.xml or the library databases, and the restored token checked against Plex before it is published.
- `test-tautulli-restore.py`: the Plex connectivity check and Tautulli's restore swap inputs.
- `test-qbittorrent-restore.py`: qBittorrent's restore swap inputs, and its staged-state and canonical-credential hooks run against fixtures.
- `test-observability-recovery.py`: both-service quiescence, the complete data archive, and the restore swap inputs: Prometheus and Grafana trees with their own uids, the staged Grafana database hook and the Grafana health hook.
- `test-karakeep-recovery.py`: one-archive backup of both persistent volumes, and the
  two restore swap paths from that archive with the owner Deploy also applies.
- `test-bookstack-recovery.py`: BookStack validates its default admin address, ensures
  its backup scheduler, and Remove clears only its validated, unshared database and login.
  Restore runs the role's restore swap (the /config owner Deploy gives it, the database
  and dump) after the recent pre-restore point check, and the guest helper only backs up,
  starting BookStack again even when the upload fails.
- `test-pre-restore-point-age.py`: the shared one-hour pre-restore point check passes a
  recent point of the target's group and refuses a stale, future, malformed, foreign or
  selected-again point.
- `test-bookstack-kubernetes.py`: BookStack keeps Docker as its default, dispatches the
  Kubernetes method to an owned namespace, and renders a default-class PVC, restricted
  recovery access, application-consistent backup CronJob and rollback-capable restore Job.
- `test-wordpress-recovery.py`: WordPress Restore runs the role's restore swap for
  `wp-content` and the database from the point's own group, after the recent pre-restore
  point check; the replaced hook points the site URLs at the target and updates or creates
  its administrator. The guest helper and its env file only back up. It also checks that
  the bind-mounted data directory is accessible to Apache's `www-data` user.
- `test-wordpress-kubernetes.py`: WordPress keeps Docker as its default, dispatches Deploy,
  Backup and Restore by `hosting.method`, and renders its default-class content claim,
  WP-CLI jobs and shared, database-consistent Kubernetes recovery inputs.
- `test-firewall-wiring.py`: OPNsense app rules and opt-in port forwards plus k3s host alias ownership and convergence against a fake API.
- `test-firewall-base.py`: base aliases and filter rules render from the documented example, validate family-specific ICMP, migrate only the legacy Green lab set, preserve foreign rules, converge, and restore a pre-write config backup after a failed readback.
- `test-proxmox-firewall-base.py`: Proxmox datacenter and node rules render from the documented example, migrate only the legacy host-firewall rule shapes, preserve per-app/foreign rules, converge, and open a configured joining node as a peer.
- `test-k8s-recovery-jobs.py`: the shared Kubernetes backup CronJob and restore Job keep the PBS client on tmpfs, read the rotated PBS fingerprint, keep the previous point through a same-day backup, preview through the client read-only, and load the database before replacing volumes; each Kubernetes app's archives, database step and stop list render through them.
- `test-shared-standards.py`: PBS client calls and installs, database dump/restore/drop, role-owned Kubernetes recovery Jobs, and a restore moving state into place or assigning ownership appear only in their shared files; every allowlist is empty. README and full-line comment mentions do not count.
- `test-restore-swap.py`: the shared restore swap on fixtures: preview changes nothing; a swap installs the point with the declared owner and mode; a failure after the stop puts every original back and starts it; a staged-data failure never stops the app; an earlier rollback copy refuses the restore; a mount swaps its contents as its owner; an incomplete rollback keeps the services stopped and the copies. With a database, the dump and the database's ownership are checked before the stop, the live database dumped after it, the archived dump loaded before the replaced hook, and the live dump loaded back on rollback; a failed database rollback keeps the services stopped. A Compose app may run the native PBS client, and an owner of `live` keeps the live directory's owner. `restore_swap_inputs.py` reads a role's inputs (inside a block too) for its own test.
- `test-mariadb-recovery.py`: the shared `server-recovery.py` for MariaDB: root-socket export, global consistency lock, PBS dispatch, manifest integrity, version guard, and same-filesystem rebuild with rollback.
- `test-mysql-recovery.py`: the shared `server-recovery.py` for MySQL: export flags, manifest, a point in the older `mysql-version.txt` format still restoring, the version guard, and reapplying the pre-restore point when a load fails.

When stdout is not a terminal and `CI` is unset (an LLM agent's tool call), the gate runs
quiet: a passing lint prints one summary line per check, a passing test run prints
`N of M ok`, and any failure prints its full log. `GATE_QUIET=0` forces the full output;
CI lists every playbook and suite.

On a Windows checkout accessed through WSL, wrap each command:

```bash
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```


The Gate workflow runs lint and test on pull requests targeting master and pushes to master.
