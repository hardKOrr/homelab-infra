# Maintenance playbooks

This directory contains operator-triggered, read-only, recovery, and scheduled day-2 entry
points. Rundeck exposes the supported operator jobs; the playbooks remain independent of
that interface.

## Operation groups

- Application operations resolve the current hosting target before restart or log access.
  Native applications use their installed `lab-*` helpers; Docker applications act on the
  Compose project; Kubernetes operations use the owning namespace.
- Configuration operations validate, read, or update user-owned runtime configuration.
- Vaultwarden operations enrol accounts, perform the verified Seed-to-Vault cutover, store
  secrets, or enter the explicit recovery path.
- Backup and restore operations use the backend-specific data path rather than treating a
  VM snapshot as application-consistent data. Role-backed Docker apps such as Immich stop
  their Compose services and archive named PostgreSQL data with explicitly owned durable
  media mounts; a restore is plan-only until an operator supplies a snapshot and overwrite.
- Status and ascent verification read state without enforcing drift.

Read [`../../../rundeck/README.md`](../../../rundeck/README.md) for the supported job
projection. Read playbook headers for exact inputs and effects.

## Disruption schedules

`maintenance.schedule` is the single automated-disruption policy. Its schema and
resolution precedence are authoritative in [`../../vars/CONTRACT.md`](../../vars/CONTRACT.md).
The resolution order is global, estate, stack, then application; a narrower declaration
replaces the inherited schedule.

Shared guests use the intersection of their applications' schedules. A `never` schedule
prevents an automated guest reboot, and non-overlapping windows report a conflict.

The resolved schedule becomes `homelab-maintenance.timer` on the guest. The guest acts when
the window opens and only when a reboot is pending. Rundeck does not poll for an open
window. `guest-maintenance.yml` is the operator action that reapplies changed timers,
reports pending reboots, or explicitly forces eligible work.

A user-triggered deployment or maintenance action is explicit operator authority; it is
not an automated event waiting for a maintenance window. Each action must still describe
and enforce its own destructive or disruptive safeguards.

## Whole-lab descent

`lab-descent.yml` arms one-shot systemd timers on the Proxmox nodes and exits before the
runner enters the blast radius. The nodes execute the descent. Proxmox startup ordering
controls the ascent. Run `verify-ascent.yml` afterwards to report guests that did not
return, unreachable guests, and Kubernetes nodes that are not ready.

Do not replace this flow with an Ansible reboot loop. The control plane cannot supervise a
reboot that shuts down its own runner.

## Shared PBS guest recovery

`backup-guest.yml` and `restore-guest.yml` are the shared `pbs_guest` route for owned
Proxmox VMs and LXCs. Rundeck exposes them under **Recover / Guests**. The exact VMID is
the recovery unit; selecting an application is not an application-only restore. A shared
Docker guest or Kubernetes node must be handled as a whole guest, with its workload scope
shown in the job output.

Backup coverage is read from the current PVE backup jobs before an on-demand `vzdump`
request. PVE returns an UPID for backup, stop, start, and restore workers, so the shared
`tasks/proxmox/wait-for-task.yml` task polls completion and rejects failure, cancellation,
or timeout. The selected PBS volume identity is kept in native `backup/vm/...` or
`backup/ct/...` form.

New restores require an unused VMID, active destination storage, target name/tags and
target-owned network configuration. They remain stopped and never perform route, identity,
controller or writer cutover. Existing restores require the exact `_+lab` target and
capture an independent target recovery point before stopping it; a failed or partial
restore leaves the target stopped and the point accessible for retry. Bind/device mounts,
physical or disabled disks, passthrough, hook resources, and external filesystems are
outside PBS guest coverage and remain explicit review items. Guest restore correctness is
not application-specific acceptance.

## Backup evidence audit

`audit-backups.yml` powers **Audit Backups** under **Manage / Lab / Health**. It reads
Proxmox guest configurations, backup jobs and backup storage visible to the configured
node. Set `include_legacy=true` to include untagged source guests without adopting or
changing them. `max_age_hours` defaults to 36; `audit_node` optionally selects another
configured delegation node.

The report distinguishes missing, stale, future-dated and fresh snapshot candidates;
unreadable evidence remains unknown. It flags LXC bind/device mounts, omitted volume
mounts, excluded VM disks and external devices. A snapshot candidate does not prove the
application's identity, database consistency, artifact integrity or successful restore.
Those fields remain explicitly unverified. The audit does not discover guest-mounted
remote filesystems or application-specific backup jobs, and pool-based schedules require
a separate membership check. Its exit status reports whether inventory collection ran,
not whether the applications are recoverable.

The audit joins the unchanged PR #81 guest document with the controller-side
[`catalog/recovery.yml`](../../../catalog/recovery.yml) inventory. That product view links
every catalog product to its recovery issue, derives the declared native method from the
product defaults, and records the PBS guest unit, shared-guest effects, external data and
credential boundary. It never infers application identity from a guest name or VMID. A
configured native schedule is not a successful artifact; artifact, integrity, external
data and restore fields stay unknown until product evidence is supplied. The user's
must-keep priority remains a separate pending selection. The one legacy source named by
the PR #81 observation is preserved as an explicit, non-adopted pending selection.

For recovery when the runner is unavailable, the same collector can run on a Proxmox
node with Python 3 and read permission: `python3 audit.py --node <node> --include-legacy`.
The source is [`../../files/recovery/audit.py`](../../files/recovery/audit.py). Only
`pvesh get` requests run; no credentials, full guest configurations or raw command errors
appear in the report.

## Bounded recovery proof

`prove-recovery.yml` is the **Recover / Drills / Prove Recovery** dispatcher. It resolves
product defaults and catalog recovery ownership, refusing native/project-managed,
rebuild-only, Kubernetes, appliance and out-of-band runner/vault/PBS units. It requires
an exact owned, running disposable guest, complete comma-separated workload acknowledgement,
and explicit active PBS/destination storage. Plan can discover workload tags with a blank
acknowledgement; execution requires the exact disclosed list. Application tags and
shared-stack membership must match the selected instance. Device/excluded disk and bind-mount coverage are refused; guest-mounted remote
filesystems and independently hosted dependencies remain unverified.

Plan is read-only and reports the sequence/scope; it creates no fixture or recovery point.
Execute invokes the owning deploy twice, requires zero second-run changes, writes and reads
A using an optional repository-owned adapter, invokes `backup-guest.yml`, identifies a newly
completed A artifact, writes/reads B and captures a distinct B artifact. It invokes unchanged
`restore-guest.yml` for plan and execution with that independent B point. Existing restores
verify running state, original identity/interfaces/onboot and serving plus A-present/B-absent.
A product without an adapter reports serving-only evidence, incomplete for durable data.

The child-playbook callback transports only explicit observations and counters to the
controller helper. Raw task diagnostics, no-log results and backup contents never become
proof output. Warnings and ignored/rescued failures prevent an unqualified evidence claim.
Failures report the stage and any captured A/B identities; artifacts and partial targets
remain for inspection and the owning Restore Guest retry route. No automatic retry or
cleanup runs. The Ntfy adapter retains uniquely identified, non-sensitive cached messages
on the application's already-authorized topic; it does not change access policy.

New destinations use the same restore seam, require an unused VMID, distinct explicit
address/MAC/name with target-only tags (no source application/stack/cluster selectors)
and one target-owned LXC `net0` with `link_down=1`, and are inspected stopped with
`onboot=0`. Multiple interfaces and VM cloud-init address layouts are refused because
the owning route cannot replace their full connectivity. Only destination inspection
follows new restore; it does not read source configuration or serving state. Serving
and fixture verification remain pending until separately authorized isolated activation; the dispatcher never claims a complete new-target data
proof. No routing or writer cutover is provided.

Optional `recovery.drill.fixture_playbook` is declared beside application defaults and
names a committed maintenance playbook, never a second product registry. Its A/B phases
write and read through the application; verify emits `serving`, `a_present`, `b_absent`
booleans after assertions without contents. Runtime instance config cannot replace method
or adapter declarations. This dispatcher is not a prerequisite for ordinary deployment,
function/convergence checks or supported Backup/Restore observations.
