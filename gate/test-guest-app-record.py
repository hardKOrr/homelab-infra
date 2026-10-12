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

    withdraw_tasks = yaml.safe_load(
        (ROOT / "ansible/tasks/unwiring/guest-record.yml").read_text()
    )
    withdraw_cmd = task_named(withdraw_tasks, "Remove this instance from the guest's tags and notes")[
        "ansible.builtin.script"
    ]["cmd"]
    assert "--vmid" in withdraw_cmd and "hostvars[item].proxmox_vmid" in withdraw_cmd

    ha_plays = yaml.safe_load(
        (ROOT / "ansible/playbooks/apps/home-assistant.yml").read_text()
    )
    ha_provision = next(play for play in ha_plays if play["name"] == "Home Assistant | Provision")
    add_host = task_named(ha_provision["tasks"], "Add the new VM to the deploy group")[
        "ansible.builtin.add_host"
    ]
    assert add_host["proxmox_vmid"] == "{{ homelabinfra_instance.vm.vmid }}"

    helper = load_helper()
    resources = [
        {"name": "home-assistant", "vmid": 1001, "node": "pve-a", "type": "qemu", "tags": "_+lab"},
        {"name": "home-assistant", "vmid": 1002, "node": "pve-b", "type": "qemu", "tags": "_+lab;_home-assistant"},
    ]
    helper.pvesh = lambda _args: (0, json.dumps(resources), "")
    selected, problem = helper.find_guest("home-assistant", "1002")
    assert problem is None and selected["vmid"] == 1002
    mismatch, problem = helper.find_guest("other-name", "1002")
    assert mismatch is None and "not other-name" in problem

    print("Guest app records: deploy and removal use the selected VMID across duplicate names")


if __name__ == "__main__":
    main()
