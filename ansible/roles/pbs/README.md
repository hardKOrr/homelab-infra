# Proxmox Backup Server

[`pbs.yml`](../../playbooks/apps/pbs.yml) owns PBS VM provisioning and deployment;
[`configure-pbs.yml`](../../tasks/bootstrap/configure-pbs.yml) owns datastore registration,
retention and PVE backup configuration. **Deploy PBS** remains individually callable.
The role installs PBS packages and verifies its API token; it does not mount, format,
detach or attach datastore disks or exports.

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

PBS has no declared application-native or project-managed recovery method and no guest
recreation/storage attachment adapter. **Prove Recovery** excludes PBS. **Remove App**
does not destroy a guest and is not a recreation path. Whole-lab decommission is also not
a PBS repair action. Installing an empty PBS is not recovery evidence.

The supported whole-guest artifact route is **Recover / Guests / Restore Guest**, owned
by [`restore-guest.yml`](../../playbooks/maintenance/restore-guest.yml). It can be used for
PBS only when an independently available PBS server/datastore supplies the artifact.
It cannot restore a failed PBS from that PBS's unavailable datastore. Before any existing
replacement, the owning observation must verify and record all of the following:

- Exact guest VMID/node, current `_+lab` ownership and instance tag, every workload,
  attached disk, datastore path, storage owner and failure domain. Account for data on
  the OS disk as well as dedicated datastore disks; deleting a managed disk deletes its data.
- Independently retained datastore contents and server configuration, named complete
  artifacts and integrity/readability evidence. Verify they survive loss of this PBS guest
  and the named storage failure domain. A PVE registration, snapshot listing or successful
  job alone is insufficient. Keep a distinct usable pre-replacement point for the target.
- Working PVE and recovery-PBS access, certificate trust/fingerprint and any encryption
  keys/passwords outside the guest and components being replaced. Read the artifact with
  those credentials/keys; a key available only from the failed server is not independent.
- The supported owner path for every datastore disk/export outside whole-guest artifact
  coverage, with exact retained volume/export identity and a single writer. No external
  mount/device or storage-owner recovery is supplied by Deploy PBS or Restore Guest.

Missing or unverified proof stops replacement. Retain the original guest, NIC/MAC,
disks, datastore and recovery points. Do not substitute a blank datastore, a boolean
acknowledgement or an artifact that is only reachable through the failed server.

## Supported artifact restore and datastore reuse

Plan **Restore Guest** with `overwrite=false`, the exact source artifact, recovery storage,
destination and target. Existing execution verifies `_+lab`, artifact access/decryption
and a distinct readable pre-restore point before stopping/replacing the target. It retains
the target NIC and identity, so it cannot repair an existing NIC mismatch. An isolated new
restore uses a free VMID and target-owned name/tags/NIC, remains stopped and leaves the source
intact. A new guest is not an automatic address, route, writer or service cutover.

After a supported restore has recovered the registered datastore and its backing data,
declare `app.datastore_mode: reuse` in the PBS instance configuration before an authorized
Deploy PBS run. It requires exactly one existing datastore registration whose path matches
`infrastructure.backups.datastore_path` ignoring trailing slashes, waits for that store to
serve, and uses the retained registration. It never sends the datastore
creation/initialisation request in this mode.
Neither mode formats disks. Default `create` remains the first-install path; it also refuses
a same-name datastore at a genuinely different path instead of silently skipping creation.
Path comparison changes neither the registration nor the declaration and does not resolve
`.`/`..`, symlinks or mount identity. Recovery mode is not proof that a backing mount
or artifacts are correct: verify their identity and reads through the storage owner first.

A fresh PBS server with only retained external chunks has no recovered registration.
`reuse` deliberately refuses it. This repository has no supported external datastore
reattachment or safe fresh-guest recreation executor; adding one requires repository work
with its storage-owner contract and regressions. Do not call the initialising `create`
path as a substitute. This limitation leaves such recreation and its live proof pending.

After recovery, list the pre-existing artifacts, restore at least one to an isolated owned
target, verify service and fixture checksums, and verify dependent PVE registrations and
credentials. Keep recovery points through acceptance. On failure, leave the target inactive,
inspect the owning job's result and retry from the independent point; never discard the
retained datastore. [#212](https://github.com/hardKOrr/homelab-infra/issues/212) owns PBS
live recovery/reattachment and artifact restore acceptance. Repository tests and #337 do
not grant lab authority or establish that acceptance.

## Verification

`python3 gate/test-pbs-reuse.py` executes the provision/preflight and datastore configuration
tasks with local recording fixtures. It covers network mismatches before mutation, matching
reuse/convergence, preserved NIC/MAC/disks/data, missing recovery capability and refusal to
initialise a retained or missing datastore in recovery mode.
`python3 gate/test-guest-recovery-contract.py` checks the supported whole-guest route's
ownership, readable artifact and independent-point guards. Neither contacts the lab.
