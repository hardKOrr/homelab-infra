#!/usr/bin/env python3
"""Guard PBS datastore volume reuse, registration and repeat-deploy behavior."""

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

from jinja2 import Environment, StrictUndefined
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

pbs_record_tasks = playbook[2]["tasks"]
reload_facts = task(pbs_record_tasks, "Reload infrastructure facts")
refresh_backup_fingerprints = task(
    pbs_record_tasks, "PBS | Refresh the fingerprint used by Kubernetes backup CronJobs"
)
assert (
    pbs_record_tasks.index(refresh_backup_fingerprints)
    > pbs_record_tasks.index(reload_facts)
)
assert refresh_backup_fingerprints["ansible.builtin.include_tasks"] == (
    "../../tasks/backup/refresh-k8s-pbs-fingerprint.yml"
)
assert (
    refresh_backup_fingerprints["vars"]["pbs_backup_fingerprint"]
    == "{{ _pbs_pve_fingerprint }}"
)
assert "kubernetes" in refresh_backup_fingerprints["when"]

refresh_fingerprints = load_yaml(
    "ansible/tasks/backup/refresh-k8s-pbs-fingerprint.yml"
)
resolve_cluster = task(
    refresh_fingerprints,
    "PBS backup trust | Resolve the Kubernetes cluster node",
)
assert (
    resolve_cluster["ansible.builtin.include_tasks"]
    == "../kubernetes/resolve-cluster.yml"
)
find_backup_namespaces = task(
    refresh_fingerprints,
    "PBS backup trust | Find namespaces with managed backup CronJobs",
)
find_argv = find_backup_namespaces["ansible.builtin.command"]["argv"]
assert find_argv[2:5] == ["get", "cronjobs", "--all-namespaces"]
assert "--selector=homelab-infra.io/pbs-fingerprint=managed" in find_argv
assert find_backup_namespaces["changed_when"] is False
refresh_namespaces = task(
    refresh_fingerprints, "PBS backup trust | Refresh each backup namespace"
)
assert "unique | list" in str(refresh_namespaces["loop"])

refresh_namespace = load_yaml(
    "ansible/tasks/backup/refresh-k8s-pbs-fingerprint-namespace.yml"
)
fingerprint_current = task(
    refresh_namespace, "PBS backup trust | Read the namespace fingerprint ConfigMap"
)
fingerprint_current_argv = fingerprint_current["ansible.builtin.command"]["argv"]
assert fingerprint_current_argv[2:5] == ["get", "configmap", "pbs-backup-trust"]
assert "--ignore-not-found" in fingerprint_current_argv
assert "--output=jsonpath={.data.fingerprint}" in fingerprint_current_argv
assert fingerprint_current["changed_when"] is False
fingerprint_apply = task(
    refresh_namespace, "PBS backup trust | Apply the current namespace fingerprint"
)
assert (
    fingerprint_apply["when"]
    == "_pbs_backup_current_fingerprint.stdout | trim != pbs_backup_fingerprint"
)
assert "data:\n  fingerprint:" in fingerprint_apply["ansible.builtin.command"]["stdin"]
assert fingerprint_apply["changed_when"] is True

provision_tasks = playbook[0]["tasks"]
provision_pre_tasks = playbook[0]["pre_tasks"]
reuse_preflight = task(provision_tasks, "Refuse provisioning without the exact retained PBS volume")
clone_index = next(i for i, item in enumerate(provision_tasks) if item.get("name") == "Provision new VM")
assert provision_tasks.index(reuse_preflight) < clone_index
disk_declaration = task(provision_pre_tasks, "Validate the optional PBS datastore disk declaration")
assert "source_vmid is defined" in str(disk_declaration["ansible.builtin.assert"]["that"])
assert "stable source_vmid reserved for the volume" in disk_declaration["ansible.builtin.assert"]["fail_msg"]
owner_query = task(provision_tasks, "Check PBS datastore owner identity against current guests")
assert owner_query["ansible.builtin.command"]["argv"][:3] == ["pvesh", "get", "/cluster/resources"]
owner_guard = task(provision_tasks, "Refuse a PBS datastore owner VMID assigned to a guest")
assert "_pbs_datastore_source_vmid | int not in" in str(owner_guard["ansible.builtin.assert"]["that"])
assert provision_tasks.index(owner_guard) < clone_index
existing_disk_include = task(provision_tasks, "Attach the retained PBS datastore disk to the existing guest")
assert "ansible_host" in existing_disk_include["vars"]["pbs_disk_guest_ip"]
provision_block = task(provision_tasks, "Provision new VM")["block"]
derive_index = next(i for i, item in enumerate(provision_block) if item.get("name") == "Derive VMID from IP")
avoid_vmid = task(provision_block, "Avoid colliding with the retained PBS datastore VMID")
assert provision_block.index(avoid_vmid) == derive_index + 1
assert provision_block.index(
    task(provision_block, "Clone the VM from the cloud-init template")
) > provision_block.index(avoid_vmid)
new_disk_include = task(provision_block, "Attach the retained PBS datastore disk to the new guest")
assert "vm_ansible_host" in new_disk_include["vars"]["pbs_disk_guest_ip"]
avoid_vmid_tasks = load_yaml("ansible/tasks/proxmox/avoid-vmid-collision.yml")
assert "avoid_vmid_source_ip == avoid_vmid_current_ip" in str(
    task(avoid_vmid_tasks, "PBS VMID | Reject an explicit VMID colliding with the datastore owner")["ansible.builtin.assert"]["that"]
)
assert "recursive=True" in str(
    task(avoid_vmid_tasks, "PBS VMID | Set the free ID in config")["ansible.builtin.set_fact"]
)
assert task(avoid_vmid_tasks, "PBS VMID | Find a free ID for an IP-derived collision")["no_log"] is True
vmid_helper = ROOT / "ansible/files/proxmox/find-free-vmid.py"
vmid_spec = importlib.util.spec_from_file_location("find_free_vmid", vmid_helper)
find_free_vmid = importlib.util.module_from_spec(vmid_spec)
vmid_spec.loader.exec_module(find_free_vmid)
assert find_free_vmid.first_free_vmid([{"vmid": 100}, {"vmid": "102"}], 100, 101) == 103
assert find_free_vmid.first_free_vmid([], 200, 201) == 200
assert find_free_vmid.first_free_vmid([{"vmid": 999_999_999}], 1_000_000_000, 100) == 101

ensure_disk = load_yaml("ansible/tasks/proxmox/ensure-pbs-datastore-disk.yml")
allocate_disk = task(ensure_disk, "PBS disk | Allocate the first datastore volume")
allocate_argv = allocate_disk["ansible.builtin.command"]["argv"]
assert allocate_argv[:2] == ["pvesm", "alloc"]
assert any("_pbs_disk_source_vmid" in arg for arg in allocate_argv)
assert "vm-{{ _pbs_disk_source_vmid }}-disk-1" in allocate_argv
active_config = task(ensure_disk, "PBS disk | Read active target VM configuration from PVE")
assert active_config["ansible.builtin.command"]["argv"][-1] == "--current"
assert active_config["no_log"] is True
assert "_pbs_disk_restart_required" in str(task(ensure_disk, "PBS disk | Find the exact retained volume")["ansible.builtin.set_fact"])
disk_identity_guard = task(ensure_disk, "PBS disk | Validate the retained volume declaration")
assert "pbs_disk_config.source_vmid is defined" in str(disk_identity_guard["ansible.builtin.assert"]["that"])
assert "pbs_disk_config.source_vmid | int != pbs_disk_vmid | int" in str(disk_identity_guard["ansible.builtin.assert"]["that"])
attach = task(ensure_disk, "PBS disk | Attach the retained volume")
assert "_pbs_disk_expected_volid" in attach["ansible.builtin.command"]["argv"][-1]
assert "discard=on" in attach["ansible.builtin.command"]["argv"][-1]
assert "_pbs_disk_current | length == 0" in str(attach["when"])
enable_discard = task(ensure_disk, "PBS disk | Enable discard on the retained volume")
assert "qm" in enable_discard["ansible.builtin.command"]["argv"]
update_args = str(enable_discard["ansible.builtin.command"]["argv"][-1])
assert "_pbs_disk_current_config.split(',')" in update_args
assert "discard=on" in update_args
assert "_pbs_disk_current | length > 0" in str(enable_discard["when"])
assert "_+lab" in str(task(ensure_disk, "PBS disk | Confirm live ownership and reject a different scsi1 disk"))
disk_config = task(ensure_disk, "PBS disk | Confirm volume, backup exclusion, stable guest serial and discard")
assert "backup=0" in str(disk_config)
assert "discard=on" in str(disk_config)
readback = task(ensure_disk, "PBS disk | Read back the attached disk configuration")
assert readback["ansible.builtin.command"]["argv"] == ["qm", "config", "{{ pbs_disk_vmid }}"]
shutdown = task(ensure_disk, "PBS disk | Gracefully stop the running VM to apply discard")
assert shutdown["ansible.builtin.command"]["argv"][:3] == ["qm", "shutdown", "{{ pbs_disk_vmid }}"]
assert "--timeout" in shutdown["ansible.builtin.command"]["argv"]
assert "--forceStop" not in shutdown["ansible.builtin.command"]["argv"]
assert "_pbs_disk_restart_required | bool" in str(shutdown["when"])
start = task(ensure_disk, "PBS disk | Start the VM with discard active")
assert start["ansible.builtin.command"]["argv"][:2] == ["qm", "start"]
assert "_pbs_disk_restart_required | bool" in str(start["when"])
running_before_restart = "'status: running' in _pbs_disk_status_before_restart.stdout"
assert running_before_restart in str(start["when"])
start_wait = task(ensure_disk, "PBS disk | Wait for the QEMU VM to start")
assert running_before_restart in str(start_wait["when"])
ssh_wait = task(ensure_disk, "PBS disk | Wait for PBS SSH after the QEMU restart")
assert ssh_wait["ansible.builtin.wait_for"]["port"] == 22
assert "_pbs_disk_restart_required | bool" in str(ssh_wait["when"])
assert running_before_restart in str(ssh_wait["when"])

pbs_role = load_yaml("ansible/roles/pbs/tasks/main.yml")
disk_include = task(pbs_role, "Configure the persistent PBS datastore disk")
service_start = task(pbs_role, "Enable and start the PBS services")
assert pbs_role.index(disk_include) < pbs_role.index(service_start)
assert pbs_role.index(task(pbs_role, "Give PBS ownership of an empty datastore directory")) < pbs_role.index(service_start)
trim_package = task(pbs_role, "Install util-linux for datastore trimming")
trim_timer = task(pbs_role, "Enable and start periodic trimming of the PBS datastore")
assert trim_package["ansible.builtin.apt"]["name"] == "util-linux"
assert "datastore_disk is defined" in str(trim_package["when"])
assert trim_timer["ansible.builtin.systemd"]["name"] == "fstrim.timer"
assert trim_timer["ansible.builtin.systemd"]["enabled"] is True
assert "datastore_disk is defined" in str(trim_timer["when"])
active_discard_check = task(pbs_role, "Confirm discard is active on the retained PBS datastore disk")
assert "discard=on" in str(active_discard_check["ansible.builtin.assert"]["that"])
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
assert "_pbs_reuse_native_supported | bool" in str(register_existing["when"])
legacy_guard = task(register_tasks, "PBS datastore reuse | Require an empty legacy config before registration")
assert "stat.size" in str(legacy_guard["ansible.builtin.assert"]["that"])
legacy_verify = task(register_tasks, "PBS datastore reuse | Verify the retained chunk store for legacy registration")
assert "verify-reused-datastore.py" in str(legacy_verify["ansible.builtin.script"])
assert legacy_verify["no_log"] is True
legacy_register = task(register_tasks, "PBS datastore reuse | Write the legacy registration without touching backup data")
assert legacy_register["ansible.builtin.copy"]["dest"] == "/etc/proxmox-backup/datastore.cfg"
assert legacy_register["ansible.builtin.copy"]["owner"] == "root"
assert legacy_register["ansible.builtin.copy"]["group"] == "backup"
assert legacy_register["ansible.builtin.copy"]["mode"] == "0640"
assert "datastore:" in legacy_register["ansible.builtin.copy"]["content"]
assert "--reuse-datastore" not in legacy_register["ansible.builtin.copy"]["content"]
post_registration = task(register_tasks, "PBS datastore reuse | Require the retained registration to be unique and mounted")
assert "_pbs_reuse_registered_after | length == 1" in str(post_registration["ansible.builtin.assert"]["that"])

helper_path = ROOT / "ansible/files/pbs/verify-reused-datastore.py"
spec = importlib.util.spec_from_file_location("verify_reused_datastore", helper_path)
verify_reused_datastore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_reused_datastore)
with tempfile.TemporaryDirectory(prefix="pbs-reuse-layout-") as tmp:
    datastore = Path(tmp) / "store"
    chunks = datastore / ".chunks"
    chunks.mkdir(parents=True)
    (datastore / ".lock").touch()
    os.chmod(datastore / ".lock", 0o600)
    for index in range(4):
        (chunks / f"{index:04x}").mkdir()
    verify_reused_datastore.verify_datastore(
        str(datastore), chunk_count=4, owner=(os.getuid(), os.getgid())
    )
    (chunks / "0003").rmdir()
    try:
        verify_reused_datastore.verify_datastore(
            str(datastore), chunk_count=4, owner=(os.getuid(), os.getgid())
        )
    except ValueError:
        pass
    else:
        raise AssertionError("legacy registration accepted an incomplete chunk directory")

configure = load_yaml("ansible/tasks/bootstrap/configure-pbs.yml")
endpoint_changed = task(configure, "PBS | Detect a changed PVE storage endpoint")
assert "_pbs_existing_storage | length == 1" in str(endpoint_changed["ansible.builtin.set_fact"])
assert "_pbs_pve_server" in str(endpoint_changed["ansible.builtin.set_fact"])
running_backups = task(configure, "PBS | Read running PVE backup tasks before replacing the storage entry")
assert running_backups["ansible.builtin.command"]["argv"] == [
    "pvesh", "get", "/cluster/tasks", "--output-format", "json"
]
assert running_backups["delegate_to"] == "{{ homelabinfra_config.proxmox.node }}"
assert running_backups["no_log"] is True
assert running_backups["changed_when"] is False
assert running_backups["failed_when"] is False
assert running_backups["become"] is False
assert "_pbs_storage_endpoint_changed | bool" in str(running_backups["when"])
query_guard = task(configure, "PBS | Require the PVE backup task query to succeed")
assert "_pbs_backup_tasks_before_storage_replacement.rc" in str(query_guard["ansible.builtin.assert"]["that"])
assert "error_class" in query_guard["ansible.builtin.assert"]["fail_msg"]
running_tasks = task(configure, "PBS | Resolve running PVE backup tasks before replacing the storage entry")
assert "selectattr('type', 'equalto', 'vzdump')" in str(running_tasks["ansible.builtin.set_fact"])
assert running_tasks["no_log"] is True
active_backup_guard = task(configure, "PBS | Refuse to replace the storage entry during a backup")
assert "_pbs_running_backup_tasks | length == 0" in str(active_backup_guard["ansible.builtin.assert"]["that"])
remove_old_storage = task(configure, "PBS | Remove the old PVE storage endpoint")
assert remove_old_storage["ansible.builtin.uri"]["method"] == "DELETE"
assert "_pbs_storage_endpoint_changed | bool" in str(remove_old_storage["when"])
assert configure.index(running_backups) < configure.index(query_guard) < configure.index(active_backup_guard) < configure.index(remove_old_storage)
storage_repair = task(configure, "PBS | Repair PVE storage endpoint and rotated API credentials")
assert storage_repair["ansible.builtin.uri"]["method"] == "PUT"
assert "server" not in storage_repair["ansible.builtin.uri"]["body"]
assert "datastore" not in storage_repair["ansible.builtin.uri"]["body"]
register_storage = task(configure, "PBS | Register PBS as a PVE storage backend")
assert "_pbs_storage_endpoint_changed | bool" in str(register_storage["when"])
assert "_pbs_storage_endpoint_changed | bool" in str(task(configure, "PBS | Decide whether the PVE storage entry needs repair")["ansible.builtin.set_fact"])
storage_check = task(configure, "PBS | Require one storage entry at the expected endpoint and datastore")
assert "| list | length == 1" in str(storage_check["ansible.builtin.assert"]["that"])
assert ".server == _pbs_pve_server" in str(storage_check["ansible.builtin.assert"]["that"])
assert ".datastore == _pbs_datastore" in str(storage_check["ansible.builtin.assert"]["that"])
assert "datastore" in str(task(configure, "PBS | Validate the existing PVE storage identity"))
assert "pbs_token_rotated" in str(task(configure, "PBS | Decide whether the PVE storage entry needs repair"))
assert "_backup_job_needs_update" in str(task(configure, "PBS | Refresh existing backup job (vmid list + schedule)")["when"])
assert task(configure, "PBS | Read current datastore configuration")["register"] == "_pbs_datastore_config"
policy_defaults = task(configure, "PBS | Resolve effective retention and schedules")["ansible.builtin.set_fact"]
assert "homelabinfra_config.infrastructure.backups.retention" in policy_defaults["_pbs_retention"]
assert "prune_schedule" in policy_defaults["_pbs_prune_schedule"]
assert "gc_schedule" in policy_defaults["_pbs_gc_schedule"]
retention_options = task(configure, "PBS | Resolve retention options for the prune job")["ansible.builtin.set_fact"]["_pbs_keep"]
assert "selectattr('key', 'match', '^keep_')" in retention_options
assert "map('replace', '_', '-')" in retention_options
gc_schedule = task(configure, "PBS | Apply only a drifted garbage-collection schedule")
assert gc_schedule["ansible.builtin.uri"]["body"] == {"gc-schedule": "{{ _pbs_gc_schedule }}"}
assert "_pbs_datastore_config.json.data['gc-schedule']" in gc_schedule["when"]
assert all(
    "keep-" not in str(item.get("ansible.builtin.uri", {}).get("body", ""))
    for item in configure
    if item.get("ansible.builtin.uri", {}).get("url")
    == "{{ _pbs_url }}/config/datastore/{{ _pbs_datastore }}"
)
prune_jobs = task(configure, "PBS | Read configured prune jobs")
assert prune_jobs["ansible.builtin.uri"]["url"] == "{{ _pbs_url }}/config/prune"
assert "selectattr('id', 'equalto', _pbs_prune_job_id)" in str(task(configure, "PBS | Resolve the managed datastore prune job")["ansible.builtin.set_fact"])
assert "(_pbs_managed_prune_jobs | first).store == _pbs_datastore" in str(task(configure, "PBS | Validate the managed prune-job identity")["ansible.builtin.assert"]["that"])
create_prune = task(configure, "PBS | Create the scheduled datastore prune job")
assert create_prune["ansible.builtin.uri"]["method"] == "POST"
assert "_pbs_prune_job_config" in create_prune["ansible.builtin.uri"]["body"]
assert "combine(_pbs_keep" in str(task(configure, "PBS | Build the desired scheduled prune-job configuration")["ansible.builtin.set_fact"])
update_prune = task(configure, "PBS | Update only drifted prune-job settings")
assert update_prune["ansible.builtin.uri"]["method"] == "PUT"
assert "_pbs_prune_job_config | dict2items" in update_prune["loop"]
assert task(configure, "PBS | Require the PVE storage backend to be active")["ansible.builtin.assert"]

assert not any("restore_bandwidth" in str(item) for item in configure)

restore_guest = load_yaml("ansible/playbooks/maintenance/restore-guest.yml")
restore_pre_tasks = restore_guest[0]["pre_tasks"]
restore_limit_validation = task(restore_pre_tasks, "Validate configured restore bandwidth")
assert "restore_bandwidth_mib_s is number" in str(restore_limit_validation["ansible.builtin.assert"]["that"])
assert "restore_bandwidth_mib_s is not boolean" in str(restore_limit_validation["ansible.builtin.assert"]["that"])
assert "| int > 0" in str(restore_limit_validation["ansible.builtin.assert"]["that"])
assert restore_limit_validation["when"] == [
    "homelabinfra_config.infrastructure.backups.restore_bandwidth_mib_s is defined",
    "homelabinfra_config.infrastructure.backups.restore_bandwidth_mib_s is not none",
]
assert task(restore_pre_tasks, "Resolve configured restore bandwidth")["ansible.builtin.set_fact"]["_rg_restore_bandwidth_mib_s"].find("default('', true)") >= 0
restore_conversion = task(restore_pre_tasks, "Convert restore bandwidth to Proxmox KiB/s")["ansible.builtin.set_fact"]
assert "* 1024" in restore_conversion["_rg_restore_bwlimit_kib"]
restore_args = task(restore_pre_tasks, "Resolve restore bandwidth command arguments")["ansible.builtin.set_fact"]
assert "'--bwlimit'" in restore_args["_rg_restore_bwlimit_args"]
assert "else []" in restore_args["_rg_restore_bwlimit_args"]

template_environment = Environment(undefined=StrictUndefined)


def render_ansible_value(expression, context, preserve_string=False):
    rendered = template_environment.from_string(expression).render(**context)
    if preserve_string:
        return rendered
    if rendered == "None":
        return None
    if rendered == "":
        return ""
    return yaml.safe_load(rendered)


def restore_bandwidth_context(configured_mib_s):
    backups = {}
    if configured_mib_s is not ...:
        backups["restore_bandwidth_mib_s"] = configured_mib_s
    context = {
        "homelabinfra_config": {"infrastructure": {"backups": backups}},
    }
    for variable, expression in task(
        restore_pre_tasks, "Resolve configured restore bandwidth"
    )["ansible.builtin.set_fact"].items():
        context[variable] = render_ansible_value(expression, context, preserve_string=True)
    for variable, expression in restore_conversion.items():
        context[variable] = render_ansible_value(expression, context, preserve_string=True)
    for variable, expression in restore_args.items():
        context[variable] = render_ansible_value(expression, context)
    return context


def restore_bandwidth_args(configured_mib_s):
    return restore_bandwidth_context(configured_mib_s)["_rg_restore_bwlimit_args"]


assert restore_bandwidth_args(64) == ["--bwlimit", "65536"]
assert restore_bandwidth_args(32) == ["--bwlimit", "32768"]
assert restore_bandwidth_args(...) == []
assert restore_bandwidth_args(None) == []

restore_guest_tasks = restore_guest[0]["tasks"]
restore_guest_block = task(restore_guest_tasks, "Restore the guest without activating the target")["block"]
restore_bandwidth_report = task(
    restore_guest_block, "Report the effective Restore Guest bandwidth limit"
)["ansible.builtin.debug"]["msg"]
assert "--bwlimit" in restore_bandwidth_report
assert "_rg_restore_bwlimit_kib" in restore_bandwidth_report
assert "Proxmox defaults apply" in restore_bandwidth_report
assert render_ansible_value(
    restore_bandwidth_report,
    restore_bandwidth_context(64),
    preserve_string=True,
) == "Restore Guest restore limit: 64 MiB/s (--bwlimit 65536 KiB/s)."
assert render_ansible_value(
    restore_bandwidth_report,
    restore_bandwidth_context(None),
    preserve_string=True,
) == "Restore Guest has no configured --bwlimit; Proxmox defaults apply."
for name in ("Restore a VM from the native PBS artifact", "Restore an LXC from the native PBS artifact"):
    command = task(restore_guest_block, name)["ansible.builtin.command"]
    argv_expression = command["argv"]
    for configured_mib_s, expected in (
        (64, ["--bwlimit", "65536"]),
        (32, ["--bwlimit", "32768"]),
        (..., []),
        (None, []),
    ):
        args = restore_bandwidth_args(configured_mib_s)
        context = {
            "_rg_target_node": "pve-node",
            "_rg_target_vmid": "200",
            "_rg_storage": "pbs-homelab",
            "_rg_artifact": "backup/vm/100/2026-01-01T00:00:00Z",
            "target_storage": "local-lvm",
            "_rg_destination": "new",
            "_rg_restore_bwlimit_args": args,
        }
        rendered_argv = render_ansible_value(argv_expression, context)
        assert rendered_argv[-len(expected):] == expected if expected else "--bwlimit" not in rendered_argv
        assert ("--bwlimit" in rendered_argv) == bool(expected)

backup_guest = load_yaml("ansible/playbooks/maintenance/backup-guest.yml")
backup_tasks = backup_guest[0]["tasks"]
backup_response = task(backup_tasks, "Normalize the PBS backup task response")
assert "regex_findall('(?m)" in backup_response["ansible.builtin.set_fact"]["_bg_task_upids"]
assert task(backup_tasks, "Require at most one PBS backup task identity")["ansible.builtin.assert"]["that"] == "_bg_task_upids | length <= 1"
assert "_bg_task_upids | first" in task(backup_tasks, "Select the PBS backup task identity")["ansible.builtin.set_fact"]["_bg_task_upid"]
async_wait = task(backup_tasks, "Wait for the asynchronous PBS backup task")
assert async_wait["when"] == "_bg_task_upid | length > 0"
sync_assert = task(backup_tasks, "Require a successful synchronous PBS backup result")
assert "INFO: Backup job finished successfully" in str(sync_assert["ansible.builtin.assert"])

restore_tasks = restore_guest[0]["tasks"]
pre_restore_tasks = task(
    restore_guest[0]["pre_tasks"], "Capture and verify an independent pre-restore recovery point"
)["block"]
pre_restore_response = task(pre_restore_tasks, "Normalize the independent target recovery task response")
assert "regex_findall('(?m)" in pre_restore_response["ansible.builtin.set_fact"]["_rg_pre_task_upids"]
assert task(pre_restore_tasks, "Require at most one independent recovery task identity")["ansible.builtin.assert"]["that"] == "_rg_pre_task_upids | length <= 1"
pre_restore_wait = task(pre_restore_tasks, "Wait for the independent target recovery point")
assert "_rg_pre_task_upid" in pre_restore_wait["vars"]["proxmox_task_upid"]
assert "_rg_pre_task_upid | length > 0" in str(pre_restore_wait["when"])
assert task(pre_restore_tasks, "Require a successful synchronous target recovery point")["ansible.builtin.assert"]["that"]
restore_tasks = task(restore_tasks, "Restore the guest without activating the target")["block"]
restore_response = task(restore_tasks, "Normalize the guest restore task response")
assert "regex_findall('(?m)" in restore_response["ansible.builtin.set_fact"]["_rg_restore_task_upids"]
assert task(restore_tasks, "Require exactly one guest restore task identity")["ansible.builtin.assert"]["that"] == "_rg_restore_task_upids | length == 1"
restore_wait = task(restore_tasks, "Wait for the guest restore task")
assert "_rg_restore_task_upid" in restore_wait["vars"]["proxmox_task_upid"]

helper = ROOT / "ansible/files/proxmox/pbs-datastore-volume-references.py"
with tempfile.TemporaryDirectory(prefix="pbs-volume-references-") as tmp:
    config_dir = Path(tmp)
    local_qemu = config_dir / "nodes/local/qemu-server"
    remote_qemu = config_dir / "nodes/remote/qemu-server"
    remote_lxc = config_dir / "nodes/remote/lxc"
    local_qemu.mkdir(parents=True)
    remote_qemu.mkdir(parents=True)
    remote_lxc.mkdir(parents=True)
    (local_qemu / "100.conf").write_text(
        "scsi1: store:vm-100-disk-1,backup=0\n"
        "[current]\n"
        "scsi1: store:vm-100-disk-1,backup=0\n"
        "[before-upgrade]\n"
        "scsi1: store:vm-100-disk-1,backup=0\n"
        "snaptime: 1700000000\n"
        "[before-discard]\n"
        "scsi1: store:vm-100-disk-1,backup=0\n",
        encoding="utf-8",
    )
    (remote_lxc / "101.conf").write_text(
        "mp0: store:vm-100-disk-1,mp=/data\n", encoding="utf-8"
    )
    (remote_qemu / "103.conf").write_text(
        "scsi1: store:vm-100-disk-1,backup=0\n", encoding="utf-8"
    )
    (remote_qemu / "104.conf").write_text(
        "[PENDING]\n"
        "scsi1: store:vm-100-disk-1,backup=0\n",
        encoding="utf-8",
    )
    (remote_qemu / "102.conf").write_text(
        "scsi1: store:vm-100-disk-10,backup=0\n", encoding="utf-8"
    )
    (remote_qemu / "104.conf").write_text(
        "scsi1: store:vm-104-disk-1,backup=0\n"
        "[current]\n"
        "scsi1: store:vm-100-disk-1,backup=0\n",
        encoding="utf-8",
    )
    (remote_qemu / "105.conf").write_text(
        "scsi1: store:vm-105-disk-1,backup=0\n"
        "[PENDING]\n"
        "scsi2: store:vm-100-disk-1,backup=0\n",
        encoding="utf-8",
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
        {"device": "scsi1", "guest_type": "qemu-server", "vmid": "103"},
        {"device": "scsi1", "guest_type": "qemu-server", "vmid": "104"},
        {"device": "scsi2", "guest_type": "qemu-server", "vmid": "105"},
        {"device": "mp0", "guest_type": "lxc", "vmid": "101"},
    ]

print("PBS recovery: retained disk, read-only reuse registration, PVE credentials and idempotence guards")
