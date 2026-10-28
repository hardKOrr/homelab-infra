#!/usr/bin/env python3
"""generate-ip.yml never hands out the address an existing guest's VMID encodes.

An ISO appliance such as PBS sets its own address, so its Proxmox config shows none and
the inventory has no ansible_host for it. Its VMID still encodes the address, and giving
that address to a new guest derives the same VMID.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
GENERATE_IP = ROOT / "ansible/tasks/network/generate-ip.yml"
ANSIBLE_PLAYBOOK = os.environ.get("GATE_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")


def allocate(clients):
    assert ANSIBLE_PLAYBOOK, "ansible-playbook executable is not configured"
    with tempfile.TemporaryDirectory(prefix="homelab-generate-ip.") as directory:
        root = Path(directory)
        # generate-ip.yml runs {{ playbook_dir }}/../../scripts/allocate-ip.py
        (root / "scripts").symlink_to(ROOT / "ansible/scripts")
        play_dir = root / "playbooks/apps"
        play_dir.mkdir(parents=True)
        inventory = {
            "all": {
                "vars": {"homelabinfra_proxmox_inventory_complete": True},
                "hosts": {"localhost": {"ansible_connection": "local"}},
                "children": {"proxmox_clients": {"hosts": clients}},
            }
        }
        (root / "inventory.yml").write_text(yaml.safe_dump(inventory), encoding="utf-8")
        (play_dir / "allocate.yml").write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {"homelabinfra_config": {"networks": {"default": {
                "cidr": "192.0.2.0/24", "gateway": "192.0.2.1", "ip_offset": 10,
            }}}},
            "tasks": [
                {"ansible.builtin.import_tasks": str(GENERATE_IP)},
                {"ansible.builtin.debug": {"msg": "ALLOCATED={{ final_ip_address }}"}},
            ],
        }]), encoding="utf-8")
        result = subprocess.run(
            [ANSIBLE_PLAYBOOK, "-i", str(root / "inventory.yml"), str(play_dir / "allocate.yml")],
            env=os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"},
            text=True, capture_output=True, check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        return output.split("ALLOCATED=")[1].split('"')[0]


def main():
    seen = {"proxmox_node": "pve-a", "proxmox_vmid": 192002010, "ansible_host": "192.0.2.10"}
    # PBS-like: no address in its config, VMID encodes 192.0.2.11
    appliance = {"proxmox_node": "pve-a", "proxmox_vmid": 192002011, "ansible_host": ""}
    # A VMID outside this network's encoding reserves nothing here.
    foreign = {"proxmox_node": "pve-a", "proxmox_vmid": 100, "ansible_host": ""}
    assert allocate({"seen": seen}) == "192.0.2.11"
    assert allocate({"seen": seen, "pbs": appliance, "other": foreign}) == "192.0.2.12"
    print("generate-ip: addresses encoded in existing VMIDs are never reallocated")


if __name__ == "__main__":
    main()
