#!/usr/bin/env python3
"""Guard the opt-in PBS second datastore, local sync and restore source selection."""

from pathlib import Path

import yaml
from remove_plan import load_playbook
import task_parts


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path):
    return task_parts.inline(ROOT / path)


def task(tasks, name):
    for item in tasks:
        if item.get("name") == name:
            return item
    raise AssertionError(f"missing task {name!r}")


defaults = load_yaml("ansible/vars/app-defaults/pbs.yml")["pbs_defaults"]
assert defaults["second_copy"]["enabled"] is False
assert defaults["second_copy"]["datastore_name"] == "homelab-copy"
assert defaults["second_copy"]["datastore_path"] == "/mnt/homelab-copy"
assert defaults["second_copy"]["pve_storage_id"] == "pbs-homelab-copy"

example = (ROOT / "config.example/apps/pbs.example.yml").read_text(encoding="utf-8")
for field in ("second_copy:", "enabled: true", "storage:", "size:", "schedule:", "retention:"):
    assert field in example

pbs = load_yaml("ansible/playbooks/apps/pbs.yml")
provision_pre = pbs[0]["pre_tasks"]
second_declaration = task(provision_pre, "Validate the optional PBS second copy declaration")
assert "second_copy.storage != app_config.proxmox.vm.datastore_disk.storage" in str(
    second_declaration["ansible.builtin.assert"]["that"]
)
assert "second_copy.size" in str(second_declaration["ansible.builtin.assert"]["that"])
assert "second_copy.schedule" in str(second_declaration["ansible.builtin.assert"]["that"])
assert "second_copy.enabled" in str(second_declaration["when"])

second_disk_identity = task(pbs[0]["tasks"], "Resolve the PBS second-copy disk identity")
assert "datastore_disk.source_vmid" in str(second_disk_identity["ansible.builtin.set_fact"])
assert "-disk-2" in str(second_disk_identity["ansible.builtin.set_fact"])
provision_tasks = pbs[0]["tasks"]
volume_mode = task(provision_tasks, "Resolve whether the PBS second-copy disk is new or retained")
assert "_pbs_second_copy_volume_exists" in str(volume_mode["ansible.builtin.set_fact"])
safe_modes = task(provision_tasks, "Select safe modes for the PBS second-copy disk and datastore")
assert "_pbs_second_copy_volume_exists" in str(safe_modes["ansible.builtin.set_fact"])
assert "'create'" in str(safe_modes["ansible.builtin.set_fact"])
assert "'attach'" in str(safe_modes["ansible.builtin.set_fact"])
pbs_configure = task(pbs[2]["tasks"], "Configure datastore, backup job and notifications")
assert "pbs_second_copy" in pbs_configure["vars"]
assert "pbs_second_copy_datastore_mode" in pbs_configure["vars"]
configure_tasks = load_yaml("ansible/tasks/bootstrap/configure-pbs.yml")
second_copy_include = task(
    configure_tasks, "PBS | Configure the optional second-copy datastore and restore source"
)
assert "second_copy.enabled" in str(second_copy_include["when"])
disable_read = task(
    configure_tasks, "PBS | Read the managed second-copy sync job when the option is disabled"
)
assert disable_read["changed_when"] is False
disable_job = task(
    configure_tasks, "PBS | Remove the managed second-copy sync schedule when the option is disabled"
)
assert disable_job["ansible.builtin.uri"]["method"] == "DELETE"
assert "second_copy_disabled_jobs | length == 1" in str(disable_job["when"])

ensure_disk = load_yaml("ansible/tasks/proxmox/ensure-pbs-datastore-disk.yml")
assert "pbs_disk_index" in str(task(ensure_disk, "PBS disk | Validate the retained volume declaration"))
assert "pbs_disk_device" in str(task(ensure_disk, "PBS disk | Validate the retained volume declaration"))
assert "_pbs_disk_index" in str(task(ensure_disk, "PBS disk | Resolve the stable storage identity"))
assert "_pbs_disk_device" in str(task(ensure_disk, "PBS disk | Attach the retained volume"))

role = load_yaml("ansible/roles/pbs/tasks/main.yml")
primary_disk = task(role, "Configure the persistent PBS datastore disk")
copy_disk = task(role, "Configure the optional PBS second-copy datastore disk")
assert primary_disk["vars"]["pbs_datastore_disk_serial"] == "homelab-pbs-store"
assert copy_disk["vars"]["pbs_datastore_disk_serial"] == "homelab-pbs-copy"
assert "second_copy.datastore_path" in copy_disk["vars"]["pbs_datastore_disk_path"]
assert "second_copy.enabled" in str(copy_disk["when"])
assert "second_copy.datastore_path" in primary_disk["vars"]["pbs_datastore_required_mounts"]

copy_tasks = load_yaml("ansible/tasks/bootstrap/configure-pbs-second-copy.yml")
datastore_identity = task(copy_tasks, "PBS second copy | Validate retained datastore identity")
assert "pbs_second_copy_datastore_mode" in str(datastore_identity["ansible.builtin.assert"]["that"])
create_datastore = task(copy_tasks, "PBS second copy | Create datastore")
assert "pbs_second_copy_datastore_mode" in str(create_datastore["when"])
sync_config = task(copy_tasks, "PBS second copy | Build desired local sync-job configuration")
sync_values = sync_config["ansible.builtin.set_fact"]["_pbs_second_copy_sync_config"]
assert sync_values["store"] == "{{ _pbs_second_copy_datastore }}"
assert sync_values["remote-store"] == "{{ _pbs_datastore }}"
assert "remote" not in sync_values
assert sync_values["sync-direction"] == "pull"
assert sync_values["remove-vanished"] == 0
prune_config = task(copy_tasks, "PBS second copy | Build desired prune-job configuration")
assert "_pbs_second_copy_keep" in str(prune_config["ansible.builtin.set_fact"])
stale_keep = task(copy_tasks, "PBS second copy | Resolve stale prune retention options")
stale_keep_expression = str(
    stale_keep["ansible.builtin.set_fact"]["_pbs_second_copy_stale_keep_keys"]
)
assert "select('match', '^keep-')" in stale_keep_expression
assert "difference(_pbs_second_copy_keep.keys() | list)" in stale_keep_expression
remove_stale_keep = task(copy_tasks, "PBS second copy | Remove stale prune retention options")
remove_stale_uri = remove_stale_keep["ansible.builtin.uri"]
assert remove_stale_uri["url"] == "{{ _pbs_url }}/config/prune/{{ _pbs_second_copy_prune_job_id }}"
assert remove_stale_uri["method"] == "PUT"
assert remove_stale_uri["body"] == {"delete": "{{ item }}"}
assert remove_stale_keep["loop"] == "{{ _pbs_second_copy_stale_keep_keys }}"
create_sync = task(copy_tasks, "PBS second copy | Create scheduled local sync job")
assert "config/sync" in create_sync["ansible.builtin.uri"]["url"]
assert create_sync["no_log"] is True
identity_guard = task(copy_tasks, "PBS second copy | Validate local sync-job identity")
assert "remote" in str(identity_guard["ansible.builtin.assert"]["that"])
assert task(copy_tasks, "PBS second copy | Create datastore")
assert task(copy_tasks, "PBS second copy | Create scheduled prune job")
assert task(copy_tasks, "PBS second copy | Register PVE storage backend for restores")

restore_app = load_playbook(ROOT / "ansible/playbooks/maintenance/restore-app.yml")
restore_app_vars = restore_app[0]["vars"]
assert "backup_datastore" in restore_app_vars["_ra_backup_datastore"]
restore_app_pre = restore_app[0]["pre_tasks"]
merge = task(restore_app_pre, "Apply the selected PBS datastore to this restore only")
assert "app_config" in merge["ansible.builtin.set_fact"]
assert "restore_target_config" in merge["ansible.builtin.set_fact"]
assert task(restore_app_pre, "Reapply the selected PBS datastore after target provisioning")

restore_job = load_yaml("rundeck/jobs/restore-app.yaml")[0]
backup_datastore_option = next(
    item for item in restore_job["options"] if item["name"] == "backup_datastore"
)
assert backup_datastore_option["required"] is False
restore_script = restore_job["sequence"]["commands"][0]["script"]
assert "RD_OPTION_BACKUP_DATASTORE" in restore_script
assert 'backup_datastore=$RD_OPTION_BACKUP_DATASTORE' in restore_script

restore_guest_job = load_yaml("rundeck/jobs/restore-guest.yaml")[0]
guest_storage = next(
    item for item in restore_guest_job["options"] if item["name"] == "backup_storage"
)
assert guest_storage["value"] == "pbs-homelab"
assert "pbs-homelab-copy" in guest_storage["description"]

print("PBS second copy: opt-in disk, local sync, retained copy and selectable restore sources")
