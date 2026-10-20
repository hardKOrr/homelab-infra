#!/usr/bin/env python3
"""Guard PBS datastore volume reuse, registration and repeat-deploy behavior."""

import json
from pathlib import Path
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def task(tasks, name):
    for item in tasks:
        if item.get("name") == name:
            return item
    raise AssertionError(f"missing task {name!r}")


defaults = load_yaml("ansible/vars/app-defaults/pbs.yml")["pbs_defaults"]
assert "datastore_disk" not in defaults["proxmox"]["vm"]

playbook = load_yaml("ansible/playbooks/apps/pbs.yml")
provision_tasks = playbook[0]["tasks"]
reuse_preflight = task(provision_tasks, "Refuse provisioning without the exact retained PBS volume")
clone_index = next(i for i, item in enumerate(provision_tasks) if item.get("name") == "Provision new VM")
assert provision_tasks.index(reuse_preflight) < clone_index
assert "source_vmid" in task(provision_tasks, "Require a stable retained volume identity for datastore reuse")["ansible.builtin.assert"]["that"][1]

ensure_disk = load_yaml("ansible/tasks/proxmox/ensure-pbs-datastore-disk.yml")
allocate_disk = task(ensure_disk, "PBS disk | Allocate the first datastore volume")
allocate_argv = allocate_disk["ansible.builtin.command"]["argv"]
assert allocate_argv[:2] == ["pvesm", "alloc"]
assert any("_pbs_disk_source_vmid" in arg for arg in allocate_argv)
assert "vm-{{ _pbs_disk_source_vmid }}-disk-1" in allocate_argv
attach = task(ensure_disk, "PBS disk | Attach the retained volume")
assert "_pbs_disk_expected_volid" in attach["ansible.builtin.command"]["argv"][-1]
assert "_pbs_disk_current | length == 0" in str(attach["when"])
assert "_+lab" in str(task(ensure_disk, "PBS disk | Confirm live ownership and reject a different scsi1 disk"))
assert "backup=0" in str(task(ensure_disk, "PBS disk | Confirm volume, backup exclusion and stable guest serial"))

pbs_role = load_yaml("ansible/roles/pbs/tasks/main.yml")
disk_include = task(pbs_role, "Configure the persistent PBS datastore disk")
service_start = task(pbs_role, "Enable and start the PBS services")
assert pbs_role.index(disk_include) < pbs_role.index(service_start)
assert pbs_role.index(task(pbs_role, "Give PBS ownership of an empty datastore directory")) < pbs_role.index(service_start)
register_reuse = task(pbs_role, "Register the retained datastore without initialising it")
assert "reuse" in str(register_reuse["when"])

disk_tasks = load_yaml("ansible/roles/pbs/tasks/datastore-disk.yml")
lsblk_read = task(disk_tasks, "PBS datastore disk | Read block devices and serials")
lsblk_check = task(disk_tasks, "PBS datastore disk | Require block-device inspection to succeed")
lsblk_resolve = task(disk_tasks, "PBS datastore disk | Resolve the unique data device")
assert lsblk_read["ansible.builtin.command"]["argv"][-1] == "NAME,SERIAL,TYPE,FSTYPE,UUID,MOUNTPOINTS"
assert lsblk_read["failed_when"] is False
assert "_pbs_lsblk.rc == 0" in str(lsblk_check["ansible.builtin.assert"]["that"])
assert "_pbs_lsblk.stderr" in lsblk_check["ansible.builtin.assert"]["fail_msg"]
assert disk_tasks.index(lsblk_read) < disk_tasks.index(lsblk_check) < disk_tasks.index(lsblk_resolve)
format_task = task(disk_tasks, "PBS datastore disk | Initialise a blank declared disk")
assert "create" in str(format_task["when"])
signature_read = task(disk_tasks, "PBS datastore disk | Read existing disk signatures")
assert "wipefs" in str(signature_read["ansible.builtin.command"])
assert "--no-act" in signature_read["ansible.builtin.command"]["argv"]
assert "reuse" in str(task(disk_tasks, "PBS datastore disk | Require existing artifacts in reuse mode")["when"])
assert "--checksum" in str(task(disk_tasks, "PBS datastore disk | Verify the copied data byte-for-byte"))
assert "RequiresMountsFor" in task(disk_tasks, "PBS datastore disk | Make both PBS services require the datastore mount")["ansible.builtin.copy"]["content"]
assert "_pbs_datastore_path_before.stat.uid" in str(task(disk_tasks, "PBS datastore disk | Preserve datastore root ownership and mode"))
assert task(pbs_role, "Give PBS ownership of an empty datastore directory")["ansible.builtin.file"]["owner"] == "backup"

register_tasks = load_yaml("ansible/roles/pbs/tasks/register-reused-datastore.yml")
register_existing = task(register_tasks, "PBS datastore reuse | Register the retained directory without initialising it")
assert register_existing["ansible.builtin.command"]["argv"][-2:] == ["--reuse-datastore", "true"]
assert "_pbs_reuse_registered | length == 0" in str(register_existing["when"])

configure = load_yaml("ansible/tasks/bootstrap/configure-pbs.yml")
storage_repair = task(configure, "PBS | Repair PVE storage endpoint and rotated API credentials")
assert storage_repair["ansible.builtin.uri"]["method"] == "PUT"
assert "datastore" not in storage_repair["ansible.builtin.uri"]["body"]
assert "datastore" in str(task(configure, "PBS | Validate the existing PVE storage identity"))
assert "pbs_token_rotated" in str(task(configure, "PBS | Decide whether the PVE storage entry needs repair"))
assert "_backup_job_needs_update" in str(task(configure, "PBS | Refresh existing backup job (vmid list + schedule)")["when"])
assert "_pbs_datastore_config.json.data[item.key]" in str(task(configure, "PBS | Apply only drifted retention settings")["when"])
assert task(configure, "PBS | Require the PVE storage backend to be active")["ansible.builtin.assert"]

backup_guest = load_yaml("ansible/playbooks/maintenance/backup-guest.yml")
backup_tasks = backup_guest[0]["tasks"]
backup_response = task(backup_tasks, "Normalize the PBS backup task response")
assert "regex_search('UPID:" in backup_response["ansible.builtin.set_fact"]["_bg_task_upid"]
async_wait = task(backup_tasks, "Wait for the asynchronous PBS backup task")
assert async_wait["when"] == "_bg_task_upid | length > 0"
sync_assert = task(backup_tasks, "Require a successful synchronous PBS backup result")
assert "INFO: Backup job finished successfully" in str(sync_assert["ansible.builtin.assert"])

helper = ROOT / "ansible/files/proxmox/pbs-datastore-volume-references.py"
with tempfile.TemporaryDirectory(prefix="pbs-volume-references-") as tmp:
    config_dir = Path(tmp)
    (config_dir / "qemu-server").mkdir()
    (config_dir / "lxc").mkdir()
    (config_dir / "qemu-server/100.conf").write_text(
        "scsi1: store:vm-100-disk-1,backup=0\n", encoding="utf-8"
    )
    (config_dir / "lxc/101.conf").write_text(
        "mp0: store:vm-100-disk-1,mp=/data\n", encoding="utf-8"
    )
    (config_dir / "qemu-server/102.conf").write_text(
        "scsi1: store:vm-100-disk-10,backup=0\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            "python3",
            str(helper),
            "store:vm-100-disk-1",
            "--config-dir",
            str(config_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == [
        {"device": "scsi1", "guest_type": "qemu-server", "vmid": "100"},
        {"device": "mp0", "guest_type": "lxc", "vmid": "101"},
    ]

print("PBS recovery: retained disk, read-only reuse registration, PVE credentials and idempotence guards")
