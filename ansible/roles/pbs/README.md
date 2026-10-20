# Proxmox Backup Server

[`pbs.yml`](../../playbooks/apps/pbs.yml) owns PBS VM provisioning and deployment;
[`configure-pbs.yml`](../../tasks/bootstrap/configure-pbs.yml) owns datastore registration,
retention and PVE backup configuration. **Deploy PBS** remains individually callable.
The role installs PBS packages and verifies its API token. An optional
`proxmox.vm.datastore_disk` declaration attaches and mounts one PVE disk at the configured
datastore path. This gives the datastore a storage identity independent of the replaceable
PBS OS disk.

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

When a fresh PBS creates a new API token, Deploy updates the existing PVE PBS storage
entry with the new endpoint, fingerprint and token, then waits for the storage to become
active. Subsequent Deploy runs update PVE storage, datastore retention and the scheduled
backup job only when their current values differ. An active PVE storage status is required
before Deploy reports success.

Datastore reuse is not proof that the backing files are complete. Verify the volume identity
through PVE, then list the pre-existing artifacts and restore a canary before removing any
fallback data or old recovery point. Restore Guest still requires an independent PBS source
when restoring the PBS VM itself.

After recovery, list the pre-existing artifacts, restore at least one to an isolated owned
target, verify service and fixture checksums, and verify dependent PVE registrations and
credentials. Keep recovery points until recovery checks pass. On failure, leave the target inactive,
inspect the owning job's result and retry from the independent point; never discard the
retained datastore.
