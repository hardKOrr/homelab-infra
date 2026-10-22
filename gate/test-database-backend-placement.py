#!/usr/bin/env python3
"""Check shared database guest selection and pinned placement defaults."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
PREPARE = ROOT / "ansible/tasks/database/resolve-backend-placement.yml"
PROVISION = ROOT / "ansible/tasks/database/provision-backend-guest.yml"
RESOLVER = ROOT / "ansible/tasks/proxmox/resolve-guest-placement.yml"
ENGINES = ("postgresql", "redis", "mariadb", "mysql", "influxdb")


def iter_tasks(value):
    if isinstance(value, list):
        for item in value:
            yield from iter_tasks(item)
    elif isinstance(value, dict):
        if "name" in value or any(
            key in value for key in ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")
        ):
            yield value
        for key in ("pre_tasks", "tasks", "block", "rescue", "always"):
            yield from iter_tasks(value.get(key, []))


def run_playbook(plays, inventory, env=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        playbook = root / "test.yml"
        playbook.write_text(yaml.safe_dump(plays, sort_keys=False), encoding="utf-8")
        inventory_path = root / "inventory.ini"
        inventory_path.write_text(inventory, encoding="utf-8")
        result = subprocess.run(
            [str(ANSIBLE), "-i", str(inventory_path), str(playbook)],
            text=True,
            capture_output=True,
            env=os.environ | {
                "ANSIBLE_STDOUT_CALLBACK": "default",
                "ANSIBLE_NOCOLOR": "1",
                **(env or {}),
            },
            check=False,
        )
        return result


def prepare_vars(instance_config, app_proxmox, existing_hosts=None, placement=None):
    proxmox = {
        "node": "control-node",
        "storage": "global-store",
        "nodes": {
            "node-2": "192.0.2.2",
            "node-lxc": "192.0.2.3",
        },
    }
    proxmox.update(placement or {})
    return {
        "instance": "database-test",
        "_instance_config": instance_config,
        "app_config": {"proxmox": app_proxmox},
        "homelabinfra_config": {"proxmox": proxmox},
        "_existing_hosts": existing_hosts or [],
    }


def check_prepare_task():
    cases = [
        (
            "instance VM placement",
            prepare_vars(
                {"proxmox": {"guest_type": "vm"}},
                {"guest_type": "lxc", "node": "node-2", "storage": "vm-store"},
            ),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_guest_type == 'vm'",
                    "_database_backend_node == 'node-2'",
                    "_database_backend_storage == 'vm-store'",
                    "app_config.proxmox.guest_type == 'vm'",
                ]}},
            ],
        ),
        (
            "legacy PostgreSQL and Redis type key",
            prepare_vars(
                {"proxmox": {"type": "vm"}},
                {"guest_type": "lxc"},
            ),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_guest_type == 'vm'",
                    "_database_backend_node == 'control-node'",
                    "_database_backend_storage == 'global-store'",
                ]}},
            ],
        ),
        (
            "type-specific LXC placement keys",
            prepare_vars(
                {"proxmox": {
                    "guest_type": "lxc",
                    "lxc": {"node": "node-lxc", "disk_volume": {"storage": "lxc-store"}},
                }},
                {
                    "guest_type": "lxc",
                    "node": "node-common",
                    "storage": "common-store",
                    "lxc": {
                        "node": "node-lxc",
                        "disk_volume": {"storage": "lxc-store"},
                    },
                },
            ),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_node == 'node-lxc'",
                    "_database_backend_storage == 'lxc-store'",
                ]}},
            ],
        ),
        (
            "neutral LXC defaults",
            prepare_vars({}, {"guest_type": "lxc"}),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_guest_type == 'lxc'",
                    "_database_backend_node == 'control-node'",
                    "_database_backend_storage == 'global-store'",
                ]}},
            ],
        ),
        (
            "classed VM defaults",
            prepare_vars(
                {"proxmox": {"guest_type": "vm"}},
                {"guest_type": "vm"},
                placement={
                    "node_roles": {"storage": "node-2", "compute": ["node-lxc"]},
                    "storage_ids": {
                        "database_vm": "database-vm-store",
                        "floating_lxc": "floating-lxc-store",
                    },
                },
            ),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_classed_placement | bool",
                    "_database_backend_node == 'node-2'",
                    "_database_backend_storage == 'database-vm-store'",
                ]}},
            ],
        ),
        (
            "classed LXC defaults",
            prepare_vars(
                {"proxmox": {"guest_type": "lxc"}},
                {"guest_type": "lxc"},
                placement={
                    "node_roles": {"storage": "node-2", "compute": ["node-lxc"]},
                    "storage_ids": {
                        "database_vm": "database-vm-store",
                        "floating_lxc": "floating-lxc-store",
                    },
                },
            ),
            [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": [
                    "_database_backend_classed_placement | bool",
                    "_database_backend_node == ''",
                    "_database_backend_storage == 'floating-lxc-store'",
                    "_database_backend_nodes_to_validate == ['node-lxc']",
                ]}},
            ],
        ),
    ]

    for name, variables, tasks in cases:
        result = run_playbook(
            [{
                "name": name,
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": variables,
                "tasks": tasks,
            }],
            "[local]\nlocalhost ansible_connection=local\n",
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"{name} failed\n{output}"

    existing_vars = prepare_vars(
        {"proxmox": {"guest_type": "lxc", "node": "unregistered-node", "storage": "old-store"}},
        {"guest_type": "lxc", "node": "unregistered-node", "storage": "old-store"},
        ["existing_guest"],
    )
    existing = run_playbook(
        [{
            "name": "reuse existing database backend without target node address",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": existing_vars,
            "tasks": [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.assert": {"that": "_database_backend_node == 'unregistered-node'"}},
            ],
        }],
        "[local]\nlocalhost ansible_connection=local\n"
        "existing_guest ansible_connection=local proxmox_vmtype=lxc\n",
    )
    output = existing.stdout + existing.stderr
    assert existing.returncode == 0, f"existing guest placement asserts were not skipped\n{output}"

    mismatch_vars = prepare_vars(
        {"proxmox": {"guest_type": "vm"}},
        {"guest_type": "vm"},
        ["existing_guest"],
    )
    mismatch_plays = [{
        "name": "mismatched existing database backend",
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": mismatch_vars,
        "tasks": [{
            "block": [
                {"ansible.builtin.include_tasks": str(PREPARE)},
                {"ansible.builtin.set_fact": {"_after_type_guard": True}},
            ],
            "rescue": [
                {"ansible.builtin.assert": {"that": [
                    "_after_type_guard is not defined",
                    "'database-test' in (ansible_failed_result | to_json)",
                    "'will not convert' in (ansible_failed_result | to_json)",
                ]}},
                {"ansible.builtin.set_fact": {"_mismatch_rejected": True}},
            ],
        }, {
            "ansible.builtin.assert": {"that": "_mismatch_rejected | default(false) | bool"}
        }],
    }]
    mismatch = run_playbook(
        mismatch_plays,
        "[local]\nlocalhost ansible_connection=local\n"
        "existing_guest ansible_connection=local proxmox_vmtype=lxc\n",
    )
    output = mismatch.stdout + mismatch.stderr
    assert mismatch.returncode == 0, f"existing type mismatch was not guarded\n{output}"


def run_pinned_resolver_case(guest_type):
    placement_class = "database_vm" if guest_type == "qemu" else "floating_lxc"
    pvesh = """#!{python}
import json, os, sys
args = sys.argv[1:]
if '/cluster/resources' in args:
    kind = args[args.index('--type') + 1]
    key = 'PINNED_NODES' if kind == 'node' else 'PINNED_STORAGES'
    print(os.environ[key])
elif any(arg.endswith('/network') for arg in args):
    print(os.environ['PINNED_NETWORK'])
elif any(arg.endswith('/status') for arg in args):
    print(os.environ['PINNED_MEMORY'])
else:
    raise SystemExit('unexpected pvesh request')
""".format(python=sys.executable)
    node_rows = [{"type": "node", "node": "node-2", "status": "online"}]
    storage_rows = [{
        "type": "storage",
        "storage": "instance-store",
        "node": "node-2",
        "status": "available",
        "shared": 0,
        "content": "images,rootdir",
    }]
    network_rows = [{
        "type": "bridge",
        "iface": "vmbr0",
        "active": 1,
        "bridge_vlan_aware": 1,
        "bridge_vids": "10 20 30",
    }]
    vars_ = {
        "homelabinfra_config": {
            "proxmox": {"node": "control-node", "storage": "global-store"},
            "networks": {"default": {"bridge": "vmbr0", "vlan": 20}},
        },
        "app_config": {"proxmox": {"node": "node-2", "storage": "instance-store"}},
        "homelabinfra_instance": {"network": {"bridge": "vmbr0", "vlan": 20}},
        "network_effective": {"bridge": "vmbr0", "vlan": 20},
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stub = root / "pvesh"
        stub.write_text(pvesh, encoding="utf-8")
        stub.chmod(0o755)
        env = {
            "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
            "PINNED_NODES": json.dumps(node_rows),
            "PINNED_STORAGES": json.dumps(storage_rows),
            "PINNED_NETWORK": json.dumps(network_rows),
            "PINNED_MEMORY": json.dumps({"memory": {"available": 2**30, "total": 2**32, "used": 2**30}}),
        }
        play = [{
            "name": f"pinned database {guest_type} placement",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": vars_,
            "tasks": [
                {"ansible.builtin.include_tasks": str(RESOLVER), "vars": {
                    "guest_placement_class": placement_class,
                    "guest_placement_database_backend": True,
                    "guest_placement_bridge": "vmbr0",
                }},
                {"ansible.builtin.assert": {"that": [
                    f"_guest_placement.guest_type == '{guest_type}'",
                    "_guest_placement.node == 'node-2'",
                    "_guest_placement.storage == 'instance-store'",
                    "homelabinfra_config.proxmox.node == 'node-2'",
                ]}},
            ],
        }]
        result = run_playbook(
            play,
            "[local]\nlocalhost ansible_connection=local\n"
            "control-node ansible_connection=local\n"
            "node-2 ansible_connection=local\n",
            env,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"pinned {guest_type} placement failed\n{output}"


def check_all_engines_share_placement():
    provision_path = "../../tasks/database/provision-backend-guest.yml"
    prepare_path = "../../tasks/database/resolve-backend-placement.yml"
    for engine in ENGINES:
        defaults = yaml.safe_load(
            (ROOT / f"ansible/vars/app-defaults/{engine}.yml").read_text(encoding="utf-8")
        )[f"{engine}_defaults"]
        assert defaults["proxmox"]["guest_type"] == "lxc"

        playbook = yaml.safe_load(
            (ROOT / f"ansible/playbooks/apps/{engine}.yml").read_text(encoding="utf-8")
        )
        provision_play = playbook[0]
        tasks = list(iter_tasks(provision_play))
        includes = [task.get("ansible.builtin.include_tasks") for task in tasks]
        assert prepare_path in includes, f"{engine} does not use shared placement normalization"
        assert provision_path in includes, f"{engine} does not use shared guest provisioning"
        assert includes.index(prepare_path) < includes.index(provision_path)

    prepare_text = PREPARE.read_text(encoding="utf-8")
    assert "guest_type" in prepare_text and "'type'" in prepare_text
    assert "homelabinfra_config.proxmox.get('node', '')" in prepare_text
    assert "homelabinfra_config.proxmox.get('storage', '')" in prepare_text
    mismatch = next(
        task for task in yaml.safe_load(prepare_text)
        if task.get("name") == "Database backend | Refuse a guest type change for an existing instance"
    )
    assert "proxmox_vmtype" in str(mismatch["ansible.builtin.assert"])
    assert "will not convert" in mismatch["ansible.builtin.assert"]["fail_msg"]

    provision_text = PROVISION.read_text(encoding="utf-8")
    assert "_database_backend_guest_type == 'lxc'" in provision_text
    assert "_database_backend_guest_type == 'vm'" in provision_text
    assert provision_text.count("proxmox_placement_database_backend:") == 3
    assert "{{ not (_database_backend_classed_placement | bool) }}" in provision_text

    provision_tasks = list(iter_tasks(yaml.safe_load(provision_text)))
    lxc_identity = next(
        task for task in provision_tasks
        if task.get("name") == "Database backend | Set LXC identity and sizing"
    )
    lxc_create = yaml.safe_load(
        (ROOT / "ansible/tasks/proxmox/lxc-create.yml").read_text(encoding="utf-8")
    )
    lxc_build_facts = next(
        task for task in lxc_create
        if task.get("name") == "Build facts for usage in proxmox lxc creation"
    )
    lxc_storage = next(
        task for task in lxc_create
        if task.get("name") == "Apply the selected LXC rootfs storage"
    )
    lxc_module_args = next(
        task for task in lxc_create
        if task.get("name") == "Build LXC module arguments from the community.proxmox.proxmox allowlist"
    )
    storage_result = run_playbook(
        [{
            "name": "database LXC storage maps to disk_volume",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "instance": "database-test",
                "_database_backend_storage": "instance-store",
                "app_config": {"proxmox": {
                    "guest_type": "lxc",
                    "node": "node-2",
                    "storage": "instance-store",
                }},
                "homelabinfra_config": {
                    "ansible": {"ssh_public_key": "fixture-key"},
                    "proxmox": {
                        "node": "control-node",
                        "storage": "global-store",
                        "api_host": "pve.example.invalid",
                        "api_port": 8006,
                        "api_user": "fixture@pve",
                        "api_token_id": "fixture-token",
                        "api_token_secret": "fixture-secret",
                    },
                },
                "homelabinfra_instance": {},
                "_guest_placement": {"node": "node-2", "storage": "instance-store"},
            },
            "tasks": [lxc_identity, lxc_build_facts, lxc_storage, lxc_module_args, {
                "ansible.builtin.assert": {"that": [
                    "'storage' not in lxc_module_args",
                    "lxc_module_args.disk_volume.storage == 'instance-store'",
                ]},
            }],
        }],
        "[local]\nlocalhost ansible_connection=local\n",
    )
    output = storage_result.stdout + storage_result.stderr
    assert storage_result.returncode == 0, f"database LXC storage mapping failed\n{output}"

    lxc_start = next(
        task for task in lxc_create if task.get("name") == "Start the LXC container"
    )
    assert lxc_start["delegate_to"] == "{{ _guest_placement.node }}"
    lxc_memory = next(
        task for task in lxc_create
        if task.get("name") == "Check node memory before creating the LXC container"
    )
    assert lxc_memory["vars"]["proxmox_memory_node"] == "{{ _guest_placement.node }}"

    vm_clone = yaml.safe_load(
        (ROOT / "ansible/tasks/proxmox/vm-clone.yml").read_text(encoding="utf-8")
    )
    vm_start = next(
        task for task in vm_clone if task.get("name") == "VM clone | Start the VM"
    )
    assert vm_start["delegate_to"] == "{{ _vmc_api.node }}"
    vm_api = next(
        task for task in vm_clone if task.get("name") == "VM clone | Set connection facts"
    )
    assert "_guest_placement.node" in vm_api["ansible.builtin.set_fact"]["_vmc_api"]["node"]

    cloud_template = yaml.safe_load(
        (ROOT / "ansible/tasks/proxmox/ensure-cloud-template.yml").read_text(encoding="utf-8")
    )
    template_builder = next(
        task for task in cloud_template
        if task.get("name") == "Cloud template | Build the template"
    )
    assert template_builder["delegate_to"] == "{{ _guest_placement.node }}"

    resolver = yaml.safe_load(RESOLVER.read_text(encoding="utf-8"))
    role_guard = next(
        task for task in resolver
        if task.get("name") == "Guest placement | Validate the declared placement role shape"
    )
    assert role_guard["when"] == "not (guest_placement_database_backend | default(false) | bool)"

    contract = (ROOT / "ansible/vars/CONTRACT.md").read_text(encoding="utf-8")
    assert "proxmox.guest_type" in contract
    assert "defaults to `config/proxmox.yml` `proxmox.node`" in contract
    assert "defaults to `config/proxmox.yml` `proxmox.storage`" in contract
    for engine in ENGINES:
        example = (ROOT / f"config.example/apps/{engine}.example.yml").read_text(encoding="utf-8")
        assert "guest_type: lxc" in example
        assert "node: <pve-node-2>" in example
        assert "storage: <pve-storage>" in example


def main():
    check_all_engines_share_placement()
    check_prepare_task()
    run_pinned_resolver_case("lxc")
    run_pinned_resolver_case("qemu")


if __name__ == "__main__":
    main()
