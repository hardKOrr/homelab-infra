# Proxmox Backup Server

[`pbs.yml`](../../playbooks/apps/pbs.yml) owns PBS VM provisioning and deployment;
[`configure-pbs.yml`](../../tasks/bootstrap/configure-pbs.yml) owns datastore registration,
retention and PVE backup configuration. **Deploy PBS** remains individually callable.
The role installs PBS packages and verifies its API token. An optional
`proxmox.vm.datastore_disk` declaration attaches and mounts one PVE disk at the configured
datastore path. This gives the datastore a storage identity independent of the replaceable
PBS OS disk.

## Optional second copy

`second_copy` is disabled by default. When enabled, it attaches a second retained disk on
`scsi2` from `second_copy.storage`, formats and mounts it at the internal path
`/mnt/homelab-copy`, and registers the `homelab-copy` PBS datastore. The storage ID must
name a different Proxmox pool from the primary datastore disk. The disk uses the existing
`proxmox.vm.datastore_disk.source_vmid` with volume slot 2, so it survives PBS guest
replacement without a second reserved VMID.

PBS manages a local pull sync job from the primary datastore to `homelab-copy`; it has no
remote. `second_copy.schedule` controls the sync schedule. The copy's prune job uses
`second_copy.retention` when set, otherwise it inherits the primary retention policy, and
garbage collection uses the primary GC schedule. PVE exposes the copy as the
`pbs-homelab-copy` storage ID for **Restore Guest**. **Restore App** accepts
`backup_datastore=homelab-copy` for a one-run source override. Neither restore choice
changes the app's saved configuration. Disabling the option stops managing the copy but
removes only its managed sync schedule. It keeps the datastore, PVE restore source, prune
policy, disk and existing backups.

`infrastructure.backups.restore_bandwidth_mib_s` optionally caps **Restore Guest** at the
per-restore limit. The value is MiB/s; Restore Guest converts it to KiB/s and passes it as
`--bwlimit` on the PVE restore request. When omitted or null, it omits the option. Deploy
does not change PBS storage or cluster-wide `datacenter.cfg` bandwidth settings.

## Reusing an owned VM

The instance tag must select one `_+lab` VM. Before adding it to the deployment group
or starting it, the play resolves the same scope/explicit-network/default inheritance
used by creation and reads its current and next-boot configurations from its inventory
node. The clone contract fixes the NIC model to `virtio`; bridge and VLAN come from that
effective network. Absent VLAN and `0` mean untagged. A mismatch reports actual and expected
fields, preserves NIC/MAC, disks and data, and stops before guest readiness or deployment.
This applies to running guests too. Matching guests retain their identity and converge.

Deploy PBS requires a nonempty bridge after network inheritance, as specified by the
[`networks` contract](../../vars/CONTRACT.md#configproxmoxyml) and enforced by Config Doctor.
A selected network may omit `bridge` when it inherits `networks.default.bridge`.
If neither declares one, both creation and reuse refuse before template creation,
guest start or deployment. The shared cloud template's `vmbr0` fallback does not supply
a declared guest bridge; Deploy PBS passes the effective bridge explicitly.

Both reads are necessary: [`qm config`](https://github.com/proxmox/pve-docs/blob/master/generated/qm.1-synopsis.adoc)
includes pending configuration unless `--current` is set. A pending correct NIC cannot
prove the currently running guest matches, and a pending incorrect NIC must not be started.
Do not change the NIC, remove its tag or delete the guest to bypass the refusal.

## Recovery boundary and prerequisites

PBS guest recovery depends on where its datastore lives. Without
`proxmox.vm.datastore_disk`, the default datastore may be on the managed OS disk; deleting
that disk deletes the datastore. **Remove App** unwires and stops PBS but does not destroy
the VM. A replacement is safe only after the exact separately retained datastore volume,
its contents and its single-writer state are verified. Whole-lab decommission is not a PBS
repair action. An empty PBS cannot recover existing artifacts.

The supported whole-guest artifact route is **Recover / Guests / Restore Guest**, owned
by [`restore-guest.yml`](../../playbooks/maintenance/restore-guest.yml). It can be used for
PBS only when an independently available PBS server/datastore supplies the artifact.
It cannot restore a failed PBS from that PBS's unavailable datastore. Before any existing
replacement, verify all of the following:

- Exact guest VMID/node, current `_+lab` ownership and instance tag, every workload,
  attached disk, datastore path, storage owner and failure domain. Account for data on
  the OS disk as well as dedicated datastore disks; deleting a managed disk deletes its data.
- Independently retained datastore contents and server configuration, named complete
  artifacts with verified integrity and readability. Verify they survive loss of this PBS guest
  and the named storage failure domain. A PVE registration, snapshot listing or successful
  job alone is insufficient. Keep a distinct usable pre-replacement point for the target.
- Working PVE and recovery-PBS access, certificate trust/fingerprint and any encryption
  keys/passwords outside the guest and components being replaced. Read the artifact with
  those credentials/keys; a key available only from the failed server is not independent.
- The supported owner path for every datastore disk/export outside whole-guest artifact
  coverage, with exact retained volume/export identity and a single writer. Deploy PBS
  reattaches only the declared PVE volume; it does not recover other mounts, exports or
  passthrough devices.

Missing prerequisites stop replacement. Retain the original guest, NIC/MAC,
disks, datastore and recovery points. Do not substitute a blank datastore, a boolean
acknowledgement or an artifact that is only reachable through the failed server.

## Supported artifact restore and datastore reuse

Plan **Restore Guest** with `overwrite=false`, the exact source artifact, recovery storage,
destination and target. Existing execution verifies `_+lab`, artifact access/decryption
and a distinct readable pre-restore point before stopping/replacing the target. It retains
the target NIC and identity, so it cannot repair an existing NIC mismatch. An isolated new
restore uses a free VMID and target-owned name/tags/NIC, remains stopped and leaves the source
intact. A new guest is not an automatic address, route, writer or service cutover.

To make a datastore independent of a PBS guest, declare
`proxmox.vm.datastore_disk.storage` and `size_gib` in the instance config. On first Deploy,
`create` formats only that declared disk if it has no filesystem, copies an existing
datastore while PBS is stopped, verifies the copy with checksums, and mounts it by UUID at
`infrastructure.backups.datastore_path`. The source directory stays in place under the
mount as a fallback until the guest is replaced. Both PBS services require that mount at
boot. Default `create` remains unchanged when no datastore disk is declared.

Reserve a PVE VMID exclusively for the datastore volume in
`proxmox.vm.datastore_disk.source_vmid`. It identifies
`storage:vm-<source_vmid>-disk-1` and must differ from every PBS guest VMID. Proxmox removes
owned disks when it destroys a guest; a separate reserved owner ID keeps this volume
independent through repeated replacements. Keep this ID unchanged. Before guest replacement,
verify the disk is separate from the OS disk, the expected volume exists and is not attached
to another guest, and the snapshots are readable. A disk still owned by a PBS guest VMID
must be migrated to a reserved owner identity before that guest is destroyed. Then set
`app.datastore_mode: reuse`. Deploy checks the exact volume before creating a replacement
VM, attaches it without allocating a replacement, requires its existing ext4 filesystem,
`.chunks` directory and snapshot metadata, and mounts it by UUID. It never formats in reuse
mode. If the new PBS guest has no datastore registration, it uses
`proxmox-backup-manager datastore create --reuse-datastore true` to register the existing
directory; the normal API create/initialise path is not used. A registration at a different
path still fails closed.

PBS 3.4 does not expose the native reuse option. On that version, Deploy verifies the
existing chunk directory, lock file, ownership and permissions, then writes only the
`datastore.cfg` registration when that file is missing or empty. It refuses to overwrite
an unrecognised datastore config and never calls the initialising create path.

When a fresh PBS creates a new API token, Deploy updates the existing PVE PBS storage
entry with the fingerprint and token, then waits for the storage to become active. The PVE
PBS storage plugin fixes the server and datastore fields after creation. If a rebuilt PBS
guest has a new server address, Deploy checks that no `vzdump` task is running, then
recreates the storage entry under the same ID and reuses the existing datastore name. The
scheduled backup job keeps its storage ID. Subsequent Deploy runs update PVE storage,
datastore retention and the scheduled backup job only when their current values differ.
An active PVE storage status is required before Deploy reports success.

Datastore reuse is not proof that the backing files are complete. Verify the volume identity
through PVE, then list the pre-existing artifacts and restore a canary before removing any
fallback data or old recovery point. Restore Guest still requires an independent PBS source
when restoring the PBS VM itself.

After recovery, list the pre-existing artifacts, restore at least one to an isolated owned
target, verify service and fixture checksums, and verify dependent PVE registrations and
credentials. Keep recovery points until recovery checks pass. On failure, leave the target inactive,
inspect the owning job's result and retry from the independent point; never discard the
retained datastore.

## Moving PBS to another node with its datastore

Use this when the PBS VM's node is unavailable but its separate ZFS datastore can be made
available to another Proxmox node. Deploy PBS in `reuse` mode so it attaches the existing
volume without formatting it. Fence a failed source node before moving or importing its
drives. Never let two PBS guests write to the source and received copies at the same time.

1. Verify that no backup, prune, or garbage-collection task is writing to PBS. Record the
   PBS backup groups and snapshot identifiers. Identify the datastore volume from
   `config/apps/pbs.yml`: `proxmox.vm.datastore_disk.storage` plus its reserved
   `source_vmid` gives
   `<pve-storage>:vm-<pbs-datastore-vmid>-disk-1`. Keep `source_vmid` unchanged; it must
   not be a guest VMID.
2. For a physical drive move, power off and fence the failed node before moving every disk
   that belongs to the ZFS pool. On the recovery node, import the pool without mounting
   filesystems, for example `zpool import -N <zfs-pool>`. Verify the pool identity and
   confirm the Proxmox storage ID is enabled for the recovery node and maps to that pool.
   Never force-import a pool while the source node can still write to it.
3. For a copy-based drill while the source is still available, resolve the exact volume
   `<pve-storage>:vm-<pbs-datastore-vmid>-disk-1`. With node-local `zfspool` storage,
   `pvesm path <pve-storage>:vm-<pbs-datastore-vmid>-disk-1` should resolve to
   `/dev/zvol/<zfs-pool>/vm-<pbs-datastore-vmid>-disk-1`; snapshot and send that zvol to the
   matching destination pool. The received zvol must keep the same
   `vm-<pbs-datastore-vmid>-disk-1` name so Proxmox exposes the same volume ID. For example,
   after replacing the placeholders with the paths from `pvesm path` and the Proxmox storage
   definition:

   ```sh
   set -o pipefail
   ssh root@<pve-node-1> zfs snapshot '<zfs-pool-1>/vm-<pbs-datastore-vmid>-disk-1@<zfs-snapshot>'
   ssh root@<pve-node-1> zfs send -p '<zfs-pool-1>/vm-<pbs-datastore-vmid>-disk-1@<zfs-snapshot>' |
     ssh root@<pve-node-2> zfs receive -u '<zfs-pool-2>/vm-<pbs-datastore-vmid>-disk-1'
   ```

   A Proxmox storage of type `zfs` is ZFS over iSCSI; its `pvesm path` is an iSCSI locator,
   not a local ZFS dataset path. For a copy from that backend, snapshot and send the zvol
   from its configured ZFS portal, then receive it into a node-local `zfspool`. Set
   `proxmox.vm.datastore_disk.storage` to that destination storage ID before Deploy PBS.
   For a physical move into the same local pool and storage ID, keep the existing storage ID.

   Confirm on the recovery node that `pvesm list <pve-storage> --vmid <pbs-datastore-vmid>`
   shows exactly one matching volume and its ZFS volume size matches the source. For a copy
   to a different storage, use its destination storage ID here. Keep the source volume
   untouched as the rollback copy.
4. Stop the old PBS guest and remove only that owned guest while preserving its separately
   owned datastore volume. For a still-reachable source, use Remove App with
   `delete_data=true` and `destroy_guest=true`; its ownership checks require the exact
   `_+lab` and `_pbs` tags. Do not continue if the datastore volume is still owned by the
   guest VMID instead of the reserved `source_vmid`:

   ```sh
   "$HOMELAB_LAB_DIR/runner-lab-run.sh" <branch> playbooks/apps/remove.yml \
     -e instance=pbs -e delete_data=true -e destroy_guest=true
   ```

   If the source Proxmox node is offline, fence it at the power level and verify `pvecm
   status` reports quorum from a surviving node. Do not bypass quorum. This stale-config
   procedure is for a non-HA PBS guest; if Proxmox HA manages it, follow the HA recovery
   procedure and confirm it cannot restart against the source datastore before continuing.
   Before retiring the stale PBS guest registration, inspect the exact
   `/etc/pve/nodes/<pve-node-1>/qemu-server/<GUEST-vmid>.conf`: its name and tags
   must identify this PBS guest, and the datastore disk must use the reserved
   `source_vmid`. Back up that one config file, then remove only that stale guest config
   with `rm -- /etc/pve/nodes/<pve-node-1>/qemu-server/<GUEST-vmid>.conf`. This removes
   only the stale VM registration; leave its disks and the retained zvol untouched. PBS's
   OS disk may be local to the failed node, so do not move the old VM config to the recovery
   node unless every disk and local resource it references is available there. Follow
   [Proxmox's failed-node recovery guidance](https://pve.proxmox.com/pve-docs/chapter-pmxcfs.html#_recovering_moving_guests_from_failed_nodes)
   for the cluster-side ownership rules. If quorum, fencing, ownership or the retained
   volume identity is uncertain, stop and restore the source node first.
5. In the private `config/apps/pbs.yml`, set `proxmox.node` to the recovery node, keep the
   selected datastore storage ID and reserved `source_vmid`, and set
   `app.datastore_mode: reuse`. A per-instance node must already be present in
   `proxmox.nodes`. Run **Deploy PBS**; its volume preflight checks the selected node before
   creating the guest, then attaches the existing disk and registers the existing
   datastore without initialising it:

   ```sh
   "$HOMELAB_LAB_DIR/runner-lab-run.sh" <branch> playbooks/apps/pbs.yml -e instance=pbs
   ```
6. Confirm PBS lists the baseline groups and snapshots. Restore an application canary from
   one of those snapshots and verify the application, then verify the refreshed PVE PBS
   storage fingerprint and managed guest VMID list. Run the scheduled backup job and
   confirm it writes a new snapshot to the moved datastore.
7. Keep the original volume until the application restore and a new scheduled backup have
   both passed. If Deploy fails, stop the replacement before changing volume attachment or
   configuration; the source volume remains the recovery point.
