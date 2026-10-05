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

Offline or unknown Proxmox nodes leave guest enumeration incomplete. Status reports that diagnostic
view without stopping at inventory parsing; ascent verification reports the offline or unknown nodes
and cannot declare a clean ascent. Guest selection and allocation still refuse incomplete
inventory. Malformed or failed API inventory reads remain fatal.

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

## Whole-lab decommission

[`decommission.yml`](decommission.yml) is the supported plan/unwire entry point. It is
separate from ordinary Remove App, which keeps its guest. The Rundeck **Decommission Lab
Preflight** job owns the operator instructions and final authority handoff. This operation
is irreversible; bootstrap reconstructs a platform, not deleted application data.

Prepare a private request file containing the creation node, exact runner VMID, and an
explicit mapping for **every** `config/apps/*.yml` instance (including `rundeck`) and every
recorded workload. Do not guess a renamed instance's product. For example:

```yaml
decommission_phase: plan
decommission_node: <pve-node>
decommission_runner_vmid: <runner-vmid>
decommission_apps:
  - {app: ntfy, instance: ntfy}
  - {app: rundeck, instance: rundeck}
```

The list is illustrative, not a lab inventory. Use a private 0700 directory and a regular
0600 request file owned by the runner's job account. The Rundeck path is
`/var/lib/rundeck/decommission/<request>.yml`. The Ansible interface is
`ansible-playbook playbooks/maintenance/decommission.yml -e @<private-request>` through
the supported environment wrapper. Plan performs no mutation: it lists exact guest
identities, configuration hashes, managed disk identities, bind/device exclusions,
created Proxmox objects and unstamped/changed/shared exclusions. It also resolves each
consumer's product/instance, exact FQDN, configured provider endpoints/routes and Kubernetes
namespace without provider mutation, and checks supported cleanup/DNS access prerequisites.
Canonical keys are identified by non-secret signatures; unrelated key contents stay private.
Each consumer's effects are rechecked against that plan before provider mutation.
The confirmation hash
binds the consumer declarations as well as the node plan. Offline nodes, unreadable
storage, unknown/orphan volumes and incomplete workload declarations refuse a clean plan.

Before unwiring, export the full user configuration and required native/PBS artifacts,
vault contents, unlock material and independent operator access **outside** every guest
being removed. Verify independent datastore capacity, identity, recoverability and
retention; a datastore inside the PBS guest's managed disk disappears with that disk.
A registration is a PVE pointer, not a datastore: withdrawing it never issues a PBS delete,
prune or format. Guest-mounted remote filesystems cannot be discovered from PVE config;
verify them independently. Bind paths, physical disks, hardware/resource mappings,
shared physical storage and independently retained archives/keys remain outside erasure
authority. Review all plan exclusions; names confer no authority to clear them.

To unwire, set `decommission_phase: unwire` and add the exact displayed
`decommission_confirmation: 'DECOMMISSION <plan hash>'` and
`decommission_retention: 'INDEPENDENT RETENTION VERIFIED'`. Preserve all provider services,
databases, vault and the registry until this phase completes. The playbook uses the
existing estate-aware inverse wiring tasks in global passes: SSO/monitoring and Forgejo
deregistration for every consumer, then every proxy route, then DNS. Provider ingress
remains available until all integration calls finish; DNS endpoints must use direct IP
addresses independent of records being removed. Unsupported hostname-dependent DNS
access refuses the handoff until corrected through its owning configuration path. It then removes
owned Kubernetes namespaces through the existing **retain data** seam. Retained PV paths
and remote storage require independent retention verification before cluster guests are
removed. It stops no guest and prunes no provider registry. Degraded Caddy, Authentik,
monitoring or namespace observations refuse the final handoff. Unsupported provider cleanup
is a refusal, not silent success. A partial unwiring pass can be rerun with the same
request after resolving the failure; providers remain available.

After **ALL CONSUMERS UNWIRED**, final authority moves to the independent operator on
the recorded creation PVE node. Save the displayed **plan object** (not its wrapper) in
a root-owned 0600 JSON file on that node. Preserve the exact reviewed repository revision
and copy its [`lab.py`](../../files/decommission/lab.py) there using the supported
operator access path. Verify no other Rundeck execution or node operation is running and
hold the workstation-wide live-operation lock throughout final observation. Run as node
root, using independent operator access rather than the platform SSH key being withdrawn:

```text
python3 lab.py --node <pve-node> --execute <private-plan.json> \
  --confirmation 'DECOMMISSION <plan hash>' \
  --retention 'INDEPENDENT RETENTION VERIFIED' \
  --unwired 'ALL CONSUMERS UNWIRED' \
  --journal <private-journal.json>
```

The helper requires PVE node/root identity, the creation node, literal acknowledgements,
a node-local operation lock, online node coverage and no active PVE tasks before each
step. It withdraws owned backup schedules before stamped PBS registration, rechecks and
stops/destroys owned guests/templates with **purge disabled** and **unreferenced-disk
sweeping disabled**, and waits for successful task completion. Exact configuration,
ownership and disk inventory are read again before destruction. It verifies guest absence
and all VMID-associated volumes on every applicable active store. A successful task with
remaining disks fails the operation; unknown leftovers require their owning storage
maintenance path and a new reviewed plan, never automatic cleanup. The runner is the last
guest removed; templates follow ordinary guests so clone consumers go first. Dedicated token,
exact user ACL (PUT with the delete flag), unshared user/role and canonical
cluster-shared platform keys are withdrawn afterwards. A non-cluster-shared key file
requires explicit per-node operator handoff and refuses automated final execution.

Retain the private plan, creation record and journal for retry. Rerunning the **same** plan
re-verifies completed steps, accepts already absent objects, checks their disk absence,
and refuses reused VMIDs, recreated completed objects, changed ownership, drift or reappearing keys.
Shared disk references refuse automatic destruction. Unrelated guests and excluded objects
are checked before each step as well as at completion. Do not erase the
journal or create a fresh plan merely to bypass a failure. An interrupted runner-last
phase resumes node-side even when Rundeck is gone. Newly discovered owned guests block
credential withdrawal. Unrelated guest configurations and excluded objects are compared
with the plan at completion. External changes require investigation rather than a clean
result. Excluded unstamped/adopted/shared objects remain explicitly reported afterwards;
only planned, proven-owned objects have an absence claim.

Finally revoke external provider/Vaultwarden credentials through their owning authority
and retire workstation/private runner credential files after retention review. Their
names and stored copies do not authorize automatic revocation or retained-key deletion.
Keep the independent recovery records needed to read retained artifacts. No bare-metal
wipe, storage formatting, device-mapping deletion or retained backup pruning is provided.
