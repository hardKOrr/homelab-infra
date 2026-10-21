#!/usr/bin/env python3
"""Keep guest app records tied to the selected Proxmox VMID when names collide."""

import importlib.util
import json
import re
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

    ha_plays = yaml.safe_load(
        (ROOT / "ansible/playbooks/apps/home-assistant.yml").read_text()
    )
    ha_provision = next(play for play in ha_plays if play["name"] == "Home Assistant | Provision")
    unique_target = task_named(ha_provision["tasks"], "Refuse ambiguous existing Home Assistant VMs")
    assert unique_target["ansible.builtin.assert"]["that"] == [
        "_ha_existing_hosts | length <= 1"
    ]
    existing_add_host = task_named(ha_provision["tasks"], "Add the existing VM to the deploy group")[
        "ansible.builtin.add_host"
    ]
    assert existing_add_host["proxmox_vmid"] == "{{ hostvars[_ha_existing_hosts[0]].proxmox_vmid }}"
    add_host = task_named(ha_provision["tasks"], "Add the new VM to the deploy group")[
        "ansible.builtin.add_host"
    ]
    assert add_host["proxmox_vmid"] == "{{ homelabinfra_instance.vm.vmid }}"

    resolver = yaml.safe_load(
        (ROOT / "ansible/tasks/proxmox/resolve-app-vm.yml").read_text()
    )
    tag_lookup = task_named(resolver, "Resolve application VM | Find hosts with the application tag")
    assert tag_lookup["ansible.builtin.include_tasks"] == "tag-group.yml"
    assert tag_lookup["vars"]["tag_group_tag"] == "_{{ resolve_app_vm_instance }}"
    name_lookup = task_named(
        resolver, "Resolve application VM | Find owned guests with the exact application name"
    )
    assert name_lookup["loop"] == "{{ groups['lab_managed'] | default([]) }}"
    assert "proxmox_name" in name_lookup["when"]
    assert "resolve_app_vm_instance" in name_lookup["when"]
    ambiguous = task_named(resolver, "Resolve application VM | Refuse ambiguous exact-name owned guests")
    assert ambiguous["ansible.builtin.assert"]["that"] == [
        "_resolve_app_vm_named_owned_hosts | length <= 1"
    ]
    unclaimed = task_named(
        resolver, "Resolve application VM | Require an unclaimed exact-name fallback guest"
    )
    assert unclaimed["when"] == [
        "_resolve_app_vm_tagged_hosts | length == 0",
        "_resolve_app_vm_named_owned_hosts | length == 1",
    ]
    unclaimed_check = unclaimed["ansible.builtin.assert"]["that"][0]
    assert "proxmox_tags_parsed" in unclaimed_check
    claim_pattern_match = re.search(r"select\('match', '([^']+)'\)", unclaimed_check)
    assert claim_pattern_match is not None
    claim_pattern = re.compile(claim_pattern_match.group(1))
    assert not any(claim_pattern.match(tag) for tag in ("_+lab", "_-gpu", "operator-note"))
    assert any(claim_pattern.match(tag) for tag in ("_ollama", "_.gpu"))
    select_guest = task_named(
        resolver, "Resolve application VM | Select the tagged or uniquely named owned guest"
    )["ansible.builtin.set_fact"]
    assert "_resolve_app_vm_tagged_hosts" in select_guest["resolve_app_vm_hosts"]
    assert "_resolve_app_vm_named_owned_hosts" in select_guest["resolve_app_vm_hosts"]

    for app, display in (("ollama", "Ollama"), ("comfyui", "ComfyUI")):
        plays = yaml.safe_load((ROOT / f"ansible/playbooks/apps/{app}.yml").read_text())
        provision = next(play for play in plays if play["name"] == f"{display} | Provision")
        task_named(provision["tasks"], f"Resolve the tagged or uniquely named owned {display} VM")
        existing_host = task_named(
            provision["tasks"], "Add the existing VM to the deploy group"
        )["ansible.builtin.add_host"]
        assert "proxmox_vmid" in existing_host
        predeploy_record = task_named(
            provision["tasks"], f"Record {display} VM identity before application deployment"
        )
        assert predeploy_record["ansible.builtin.include_tasks"].endswith(
            "../../tasks/proxmox/record-app-on-guest.yml"
        )
        assert predeploy_record["vars"]["record_guest_kind"] == "docker"
        assert predeploy_record["vars"]["record_guest_require_success"] is True
        assert predeploy_record["when"] == f"_{app}_needs_identity_record"

        create_identity = task_named(
            provision["tasks"], f"Set the {display} VM identity and ownership tags"
        )["ansible.builtin.set_fact"]["homelabinfra_config"]
        assert "['_' ~ instance]" in create_identity

        wire = next(play for play in plays if play["name"] == f"{display} | Wire")
        wire_record = task_named(wire["tasks"], f"Record {display} on its dedicated Docker VM")
        assert wire_record["vars"]["record_guest_kind"] == "docker"

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
