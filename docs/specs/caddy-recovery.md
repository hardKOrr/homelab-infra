# Caddy recovery

## Method decision

Caddy's disposition is **PBS guest only**. The shared `pbs_guest` route in
[`backup-guest.yml`](../../ansible/playbooks/maintenance/backup-guest.yml) and
[`restore-guest.yml`](../../ansible/playbooks/maintenance/restore-guest.yml) is the
supported backup and restore method for the Caddy LXC, and it accepts both
`destination: new` and `destination: existing`.

There is deliberately no Caddy application `Backup` or `Restore` job. The generic
application action would be misleading for this native LXC: Caddy has no documented
application export/import command, and the existing `--resume` behavior is a runtime
persistence/resume mechanism, not a backup format. A project-managed archive would
also duplicate the complete guest snapshot while still needing the shared guest's
Proxmox identity and network recovery. `native` and `project_managed` are therefore
unsupported and are not declared. `rebuild-only` is not selected: Caddy's data and
configuration directories contain durable TLS and active-routing state, so the shared
guest artifact is the less lossy method.

This decision applies to the role as currently implemented: a Debian 12 LXC running
the official Caddy apt package, with the installed version recorded by
`/etc/ansible/facts.d/caddy.fact`. The apt source tracks the current stable package
rather than pinning a version; recovery must preserve or deliberately re-establish a
compatible Caddy major version and the DNS provider modules installed by the role.

Supporting official documentation for Caddy 2:

- [API and `--resume`](https://caddyserver.com/docs/api): the admin API persists the
  last active configuration and `caddy run --resume` replays it after restart.
- [Caddy conventions](https://caddyserver.com/docs/conventions): the data directory
  contains certificates, private keys, OCSP staples and other required state; it is
  not a disposable cache. The configuration directory stores the autosaved config.
- [Running Caddy](https://caddyserver.com/docs/running): the packaged Linux service
  uses `/var/lib/caddy/.local/share/caddy` for data and
  `/var/lib/caddy/.config/caddy` for autosaved configuration.
- [Automatic HTTPS and DNS challenge](https://caddyserver.com/docs/automatic-https):
  DNS-01 requires DNS-provider credentials and avoids requiring the Caddy host to be
  reachable from the public internet.

The role's current implementation and these Caddy 2 documents were reviewed for this
contract on 2026-09-13. A live installed version, artifact identity, schedule result
and restore result remain runtime evidence; this repository does not invent those
values.

## Recovery unit and shared-workload effects

The recovery unit is the **complete, project-owned Caddy LXC**, identified by its
exact Proxmox VMID and `_+lab` ownership tag. Caddy is the one lab-wide TLS edge and
carries the explicit `_.shared` tag. It fronts every application route across every
estate; it is not an isolated application guest.

Replacing the existing Caddy guest temporarily interrupts HTTPS and HTTP access to
all routed applications, including Authentik-gated routes. Backend guests, databases,
SSO data, monitoring data and DNS records are not modified by this restore. A failed
restore leaves the Caddy target stopped or partially restored and does not delete the
source artifact or unrelated workloads. A new target must remain on an isolated
network and must not inherit the production address, DNS identity or cutover role.

The shared guest job prints the guest/workload scope and proves Proxmox guest identity,
storage and connectivity. It does not claim that every application behind Caddy is
healthy; application-specific route checks below are required before trusting a
restored edge.

## Captured state and consistency

The PBS guest snapshot captures the Caddy LXC root filesystem and its guest-owned
storage as one native `backup/ct/<vmid>/vzdump-lxc-...` artifact. The relevant state is:

- `/etc/caddy/caddy.json`, the role-authored JSON seed containing the admin bind,
  listeners and per-estate TLS automation policies;
- `/var/lib/caddy/.config/caddy/autosave.json`, the Caddy-owned last active
  configuration, including routes written through the admin API;
- `/var/lib/caddy/.local/share/caddy`, including managed certificates, ACME account
  material, private keys, OCSP state and the internal CA when `tls_mode: internal` is
  used;
- the Caddy binary, installed DNS provider modules, systemd unit/drop-in, and the
  role's maintenance scripts; and
- ownership, modes and the guest's version fact, so the recovered service can resume
  with the same permissions and module set.

Caddy's `--resume` autosave is the consistency boundary for routes: the role writes
routes through the admin API and Caddy persists the last accepted configuration.
Before an existing-target restore, the shared method captures and verifies an
independent target PBS point. The target is stopped before replacement; Proxmox then
restores the guest artifact and waits for the asynchronous task to finish. Do not
copy individual Caddy files while the service is running and call that an
application-consistent backup.

The canonical inputs outside the guest remain part of recovery planning:

- `config/apps/<instance>.yml` and `config/infrastructure.yml`, including the target
  network, domain/estate declarations, access CIDRs and TLS mode;
- the Vaultwarden `homelab-infra/reverse_proxy` DNS-challenge credential, or the
  explicitly authorized pre-cutover Seed credential, for renewal or a post-restore
  role reconciliation; and
- the service registry and the intended application upstreams that the wiring tasks
  used to create the route table.

The snapshot preserves the current generated route table, but it does not create or
repair an application backend, an Authentik instance, an Uptime Kuma monitor, a DNS
record, a firewall rule or a Vaultwarden item. Those dependencies must already exist
or be recovered independently before the restored edge is trusted.

## Exclusions and required external material

The shared PBS contract excludes LXC bind/device mounts, physical devices, disabled
or omitted guest disks, passthrough resources and hook resources. The current Caddy
defaults declare no external data mount, so there is no second Caddy filesystem to
back up. The following remain external requirements rather than hidden archive
contents:

- Proxmox/PBS access, the PBS datastore, its certificate/fingerprint and any
  encryption/decryption key;
- the target Proxmox node, active target storage, free VMID/address and target-owned
  network configuration;
- the DNS provider zone and API credential for ACME DNS-01, when `tls_mode: acme`;
- trusted distribution of `root.crt` to clients, when `tls_mode: internal`;
- every routed upstream application and its own recovery unit; and
- the SSO, DNS, monitoring and firewall services whose connections Caddy proxies.

The DNS provider credential is sensitive and is never printed in recovery evidence.
Missing PBS credentials, unavailable target storage, missing DNS material needed for
renewal, an absent upstream, or a missing external dependency is a failed/incomplete
recovery—not a successful Caddy process start.

## Both destinations

### Restore to New Instance

Use **Restore Guest** with the source Caddy VMID and a native PBS artifact, select
`destination: new`, and provide an unused VMID, target-owned storage, name, tags,
network and MAC. The shared route validates collisions and artifact readability before
creating anything, restores with a unique identity and `start=0`, and leaves the
target stopped. Keep the source Caddy guest unreachable to the restore operation.

Before starting an isolated target, verify its target-specific address/admin bind and
route test plan. Use a lab-only address or `curl --resolve` against the isolated target
and never publish a production DNS record or point the production reverse-proxy
registry at it. Verify representative routes by checking the admin API route IDs,
upstream dial targets, `remote_ip` ranges for `routing.access: internal`, and an HTTPS
request with the expected certificate. Verify an allowed and a denied client source;
do not use a public ACME request for this drill. A new target is not a production
cutover, even when it contains the source's route table.

### Restore Existing Instance

Use **Restore Guest** with the exact managed Caddy VMID, select
`destination: existing`, and use plan mode first. With `overwrite=true`, the shared
route verifies the selected artifact and an independent pre-restore PBS point before
stopping Caddy. It restores only that exact guest and returns it to its previous
running state only after Proxmox identity, storage and status checks pass.

The product acceptance transition is **A → B → restore A**: capture Caddy state A,
add a distinguishable B-only route/access rule in the disposable target, capture and
retain the independent B point, restore A, and prove that A's representative route,
certificate state and access policy return while the B-only route disappears. If the
restore is interrupted after replacement, leave Caddy inactive, recover B from the
retained point, and retry A only after the target and both artifacts are readable.
All routed backends and shared services are in the affected scope; no backend data,
DNS record, certificate authority or source guest is cleaned up by this test.

## Failure matrix and evidence

The shared PBS fixture and focused Caddy contract test cover both destinations and
exercise these refusal boundaries before mutation: wrong VMID/target, VM/LXC kind
mismatch, collision or source-owned storage, corrupt/incomplete/unreadable artifact,
incompatible target, missing PBS/decryption material, missing external DNS/storage
requirements, interrupted replacement, and deterministic retry from the retained
point. A successful fixture result asserts route state, certificate state, intended
upstream connections, target identity and access policy—not only a running service.

`backup-guest.yml` inspects `/cluster/backup` and the selected PBS storage before an
on-demand backup, then waits for the PVE task and requires a readable native artifact.
`audit-backups.yml` reports missing, stale, fresh and unreadable scheduled candidates.
There is no Caddy-specific scheduler in the role defaults. Until an authorized live
audit supplies the exact current PVE job and recent artifact, schedule, integrity,
credential and restore evidence remain **unknown**, not successful by declaration.

Repository/fixture evidence is the closure scope for this issue. Live Caddy validation
is deferred to the existing [recovery acceptance roll-up #80](https://github.com/hardKOrr/homelab-infra/issues/80)
with an authorized isolated source/destination. It must record the installed Caddy
version, native PBS artifact identity, both destination results, route/access
assertions, and final target state. No production cutover, source shutdown, or cleanup
is implied.
