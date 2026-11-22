#!/usr/bin/env python3
"""Keep guest app records tied to the selected Proxmox VMID when names collide."""

import importlib.util
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def task_named(node, name):
    if isinstance(node, dict):
        if node.get("name") == name:
            return node
        for value in node.values():
            try:
                return task_named(value, name)
            except StopIteration:
                pass
    elif isinstance(node, list):
        for value in node:
            try:
                return task_named(value, name)
            except StopIteration:
                pass
    raise StopIteration(name)


def load_helper():
    path = ROOT / "ansible/files/proxmox/guest-app-record.py"
    spec = importlib.util.spec_from_file_location("guest_app_record", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    record_tasks = yaml.safe_load(
        (ROOT / "ansible/tasks/proxmox/record-app-on-guest.yml").read_text()
    )
    record_cmd = task_named(record_tasks, "Merge this instance into the guest's tags and notes")[
        "ansible.builtin.script"
    ]["cmd"]
    assert "--vmid" in record_cmd and "hostvars[item].proxmox_vmid" in record_cmd
    assert "--guest" in record_cmd and "hostvars[item].proxmox_name" in record_cmd

    required_record = task_named(
        record_tasks, "Require an application guest before recording when success is required"
    )
    assert "record_guest_require_success" in required_record["when"]
    merge_record = task_named(record_tasks, "Merge this instance into the guest's tags and notes")
    assert "record_guest_require_success" in merge_record["failed_when"]
    assert "record_guest_require_success" in merge_record["ignore_unreachable"]

    withdraw_tasks = yaml.safe_load(
        (ROOT / "ansible/tasks/unwiring/guest-record.yml").read_text()
    )
    withdraw_cmd = task_named(withdraw_tasks, "Remove this instance from the guest's tags and notes")[
        "ansible.builtin.script"
    ]["cmd"]
    assert "--vmid" in withdraw_cmd and "hostvars[item].proxmox_vmid" in withdraw_cmd
    assert "--guest" in withdraw_cmd and "hostvars[item].proxmox_name" in withdraw_cmd

    # tasks/proxmox/resolve-instance-guest.yml itself is exercised by
    # gate/test-instance-guest-reuse.py. The own-VM apps (Ollama, ComfyUI, Home Assistant)
    # share tasks/proxmox/provision-app-vm.yml.
    vm_tasks = yaml.safe_load((ROOT / "ansible/tasks/proxmox/provision-app-vm.yml").read_text())
    task_named(vm_tasks, "Resolve the tagged or uniquely named owned {{ app_vm_display }} VM")
    existing_host = task_named(vm_tasks, "Add the existing VM to the deploy group")[
        "ansible.builtin.add_host"
    ]
    assert existing_host["proxmox_vmid"] == "{{ hostvars[_app_vm_existing_hosts[0]].proxmox_vmid }}"
    new_vm = task_named(vm_tasks, "Provision a new {{ app_vm_display }} VM")["block"]
    add_host = task_named(new_vm, "Add the new VM to the deploy group")["ansible.builtin.add_host"]
    assert add_host["proxmox_vmid"] == "{{ homelabinfra_instance.vm.vmid }}"
    predeploy_record = task_named(
        vm_tasks, "Record {{ app_vm_display }} VM identity before application deployment"
    )
    assert predeploy_record["ansible.builtin.include_tasks"].endswith("record-app-on-guest.yml")
    assert predeploy_record["vars"]["record_guest_kind"] == "docker"
    assert predeploy_record["vars"]["record_guest_require_success"] is True
    assert predeploy_record["when"] == "_app_vm_needs_identity_record"
    for app in ("ollama", "comfyui", "home-assistant"):
        hook = (ROOT / f"ansible/roles/{app}/tasks/deploy-provision.yml").read_text()
        assert "tasks/proxmox/provision-app-vm.yml" in hook, app
    create_identity = task_named(new_vm, "Set the {{ app_vm_display }} VM identity and ownership tags")[
        "ansible.builtin.set_fact"
    ]["homelabinfra_config"]
    assert "['_' ~ instance]" in create_identity
    unique_target = task_named(vm_tasks, "Refuse ambiguous existing {{ app_vm_display }} VMs")
    assert unique_target["ansible.builtin.assert"]["that"] == ["_app_vm_existing_hosts | length <= 1"]

    helper = load_helper()
    resources = [
        {"name": "home-assistant", "vmid": 1001, "node": "pve-a", "type": "qemu", "tags": "_+lab"},
        {"name": "home-assistant", "vmid": 1002, "node": "pve-b", "type": "qemu", "tags": "_+lab;_home-assistant"},
    ]
    helper.pvesh = lambda _args: (0, json.dumps(resources), "")
    selected, problem = helper.find_guest("home-assistant", "1002")
    assert problem is None and selected["vmid"] == 1002
    selected, problem = helper.find_guest("home-assistant-1002", "1002")
    assert selected is None and "not home-assistant-1002" in problem
    mismatch, problem = helper.find_guest("other-name", "1002")
    assert mismatch is None and "not other-name" in problem

    print("Guest app records: tagged reuse, unique-name recovery, and required pre-deploy identity")


if __name__ == "__main__":
    main()
