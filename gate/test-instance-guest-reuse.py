#!/usr/bin/env python3
"""Every app with its own guest finds it again after Remove withdrew its instance tag.

Runs tasks/proxmox/resolve-instance-guest.yml against inventory fixtures, then checks that
each playbook that deploys onto the instance's own guest selects it through that file
instead of an inline tag-group lookup, which cannot see a guest Remove kept.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "ansible/tasks/proxmox/resolve-instance-guest.yml"
APP_PLAYBOOKS = ROOT / "ansible/playbooks/apps"
ANSIBLE_PLAYBOOK = os.environ.get("GATE_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
# An inline own-guest lookup: `_<something>existing_hosts` straight from the tag group.
INLINE_LOOKUP = re.compile(r"existing_hosts\W+\{\{\s*groups\[tag_group_name\]")


def guest(name="ntfy", vmtype="lxc", tags=None):
    return {
        "proxmox_name": name,
        "proxmox_vmtype": vmtype,
        "proxmox_vmid": 402,
        "proxmox_node": "pve-a",
        "proxmox_tags_parsed": tags if tags is not None else ["_+lab", "_-debian"],
    }


def run_case(guests, expected=None, failure=None, guest_type=""):
    assert ANSIBLE_PLAYBOOK, "ansible-playbook executable is not configured"
    with tempfile.TemporaryDirectory(prefix="homelab-instance-guest.") as directory:
        root = Path(directory)
        tagged = {
            host: None for host, values in guests.items()
            if "_ntfy" in values["proxmox_tags_parsed"]
        }
        inventory = {
            "all": {
                "vars": {"homelabinfra_proxmox_inventory_complete": True},
                "hosts": {"localhost": {"ansible_connection": "local"}},
                "children": {
                    "lab_managed": {"hosts": guests},
                    # tag-group.yml's group name for `_ntfy`
                    "lab_app_ntfy": {"hosts": tagged},
                },
            }
        }
        (root / "inventory.yml").write_text(yaml.safe_dump(inventory), encoding="utf-8")
        variables = {"resolve_guest_instance": "ntfy"}
        if guest_type:
            variables["resolve_guest_type"] = guest_type
        (root / "resolve.yml").write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": variables,
            "tasks": [
                {"ansible.builtin.include_tasks": str(RESOLVER)},
                {"ansible.builtin.assert": {"that": [f"resolve_guest_hosts == {expected!r}"]}},
            ],
        }]), encoding="utf-8")
        result = subprocess.run(
            [ANSIBLE_PLAYBOOK, "-i", str(root / "inventory.yml"), str(root / "resolve.yml")],
            env=os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"},
            text=True, capture_output=True, check=False,
        )
        # The repository's callback wraps long messages; compare whitespace-normalized text.
        output = " ".join((result.stdout + result.stderr).split())
        if failure is not None:
            assert result.returncode != 0 and failure in output, output
        else:
            assert result.returncode == 0, output


def check_resolver():
    run_case({"ntfy": guest(tags=["_+lab", "_ntfy"])}, ["ntfy"])
    run_case({"ntfy": guest()}, ["ntfy"])
    run_case({"ntfy": guest()}, ["ntfy"], guest_type="lxc")
    # A Kubernetes app record tags every cluster VM with _<instance>. Switching back to
    # the native LXC must ignore those tagged VMs and still reuse its exact-name LXC.
    run_case({
        "k3s-1": guest("k3s-1", vmtype="qemu", tags=["_+lab", "_ntfy"]),
        "ntfy": guest(),
    }, ["ntfy"], guest_type="lxc")
    run_case({
        "k3s-1": guest("k3s-1", vmtype="qemu", tags=["_+lab", "_ntfy"]),
        "ntfy": guest(tags=["_+lab", "_ntfy"]),
    }, ["ntfy"], guest_type="lxc")
    run_case({
        "k3s-1": guest("k3s-1", vmtype="qemu", tags=["_+lab", "_ntfy"]),
        "ntfy": guest(tags=["_+lab", "_ntfy"]),
    }, ["k3s-1"], guest_type="vm")
    run_case({
        "k3s-1": guest("k3s-1", vmtype="qemu", tags=["_+lab", "_ntfy"]),
        "ntfy": guest(tags=["_+lab", "_ntfy"]),
    }, failure="Multiple guests match the instance or stack tag")
    run_case({
        "k3s-1": guest("k3s-1", vmtype="qemu", tags=["_+lab", "_ntfy"]),
    }, [], guest_type="lxc")
    run_case({"other": guest("ntfy-other")}, [])
    run_case({"ntfy-a": guest(), "ntfy-b": guest()}, failure="Multiple _+lab guests are named ntfy")
    run_case({"ntfy": guest(vmtype="qemu")}, failure="Refusing to adopt it", guest_type="lxc")
    run_case({"ntfy": guest(tags=["_+lab", "_another-app"])}, failure="Refusing to adopt it")
    run_case({"ntfy": guest(tags=["_+lab", "_.stack+media"])}, failure="Refusing to adopt it")


def check_playbooks_use_it():
    users = []
    for playbook in sorted(APP_PLAYBOOKS.glob("*.yml")):
        text = playbook.read_text(encoding="utf-8")
        assert not INLINE_LOOKUP.search(text), (
            f"{playbook.relative_to(ROOT)}: select the instance's own guest with "
            "tasks/proxmox/resolve-instance-guest.yml, which also finds a guest Remove kept"
        )
        if "name: _template-native" in text:
            text += (ROOT / "ansible/roles/_template-native/tasks/provision.yml").read_text()
        if "tasks/proxmox/resolve-instance-guest.yml" in text:
            users.append(playbook.stem)
    assert {"ntfy", "vaultwarden", "ollama", "comfyui", "postgresql", "mariadb"} <= set(users), users


def main():
    check_resolver()
    check_playbooks_use_it()
    print("Instance guest reuse: shared resolver cases and every own-guest playbook use it")


if __name__ == "__main__":
    main()
