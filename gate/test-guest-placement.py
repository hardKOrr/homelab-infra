#!/usr/bin/env python3
"""Exercise the guest placement resolver with a local pvesh fixture."""

import json
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
RESOLVER = REPO / "ansible/tasks/proxmox/resolve-guest-placement.yml"
VM_HOST_MOUNTS = REPO / "ansible/tasks/proxmox/attach-host-mounts-vm.yml"
BOOTSTRAP = REPO / "rundeck/bootstrap-rundeck.sh"


def verify_virtiofs_mount_contract():
    requirements = yaml.safe_load((REPO / "ansible/requirements.yml").read_text())
    collections = {item["name"]: item["version"] for item in requirements["collections"]}
    assert collections.get("ansible.posix") == "2.2.2"

    task_source = VM_HOST_MOUNTS.read_text()
    assert "ansible.posix.mount:" in task_source
    assert "ansible.builtin.mount:" not in task_source
    assert "ansible.posix" in BOOTSTRAP.read_text()

    tasks = yaml.safe_load(task_source)
    mapping_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Create only missing directory mappings"
    )
    mapping_id_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Build stable directory mapping IDs"
    )
    mapping_verify_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Refuse an existing mapping with a different compute path"
    )
    task_names = [task.get("name") for task in tasks]
    ssh_wait = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Wait for guest SSH after virtiofs device changes"
    )
    assert task_names.index(ssh_wait["name"]) < task_names.index(
        "VM host mounts | Verify existing guest fstab entry for each path"
    )
    assert "ansible.builtin.wait_for" in ssh_wait
    assert "hostvars[attach_mounts_guest].ansible_host" in ssh_wait["ansible.builtin.wait_for"]["host"]
    assert "_vmfs_missing_devices | length > 0" in ssh_wait["when"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stub = root / "pvesh"
        record = root / "argv.json"
        stub.write_text(
            f"#!{ANSIBLE.parent / 'python'}\n"
            "import json, os, sys\n"
            "json.dump(sys.argv[1:], open(os.environ['GUEST_MAPPING_ARGV_FILE'], 'w'))\n"
            "print('[]')\n"
        )
        stub.chmod(0o755)
        playbook = root / "mapping.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "_vmfs_directory_mappings": [{"id": "d123", "host": "/mnt/media"}],
                "_vmfs_compute_nodes": ["compute-3", "compute-2"],
                "_vmfs_mapping_list": {"stdout": "[]"},
                "homelabinfra_config": {"proxmox": {"node": "localhost"}},
            },
            "environment": {"PATH": str(root) + os.pathsep + os.environ["PATH"]},
            "tasks": [mapping_task],
        }]))
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
            "GUEST_MAPPING_ARGV_FILE": str(record),
        }
        result = subprocess.run(
            [str(ANSIBLE), "-i", str(inventory), str(playbook)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"virtiofs mapping command failed\n{output}"
        assert json.loads(record.read_text()) == [
            "create", "/cluster/mapping/dir", "--id", "d123",
            "--map", "node=compute-3,path=/mnt/media",
            "--map", "node=compute-2,path=/mnt/media",
        ], "virtiofs directory mapping arguments were not passed separately"

    expected_id = "d" + hashlib.sha1(b"/mnt/media|compute-2|compute-3").hexdigest()[:12]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        playbook = root / "mapping-id.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "_vmfs_directory_mappings": [],
                "_vmfs_host_paths": ["/mnt/media"],
                "_vmfs_compute_nodes": ["compute-3", "compute-2"],
            },
            "tasks": [mapping_id_task, {
                "ansible.builtin.assert": {
                    "that": [
                        "_vmfs_directory_mappings | length == 1",
                        f"_vmfs_directory_mappings[0].id == {json.dumps(expected_id)}",
                        "_vmfs_directory_mappings[0].id is match('^d[0-9a-f]{12}$')",
                    ]
                }
            }],
        }]))
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        result = subprocess.run(
            [str(ANSIBLE), "-i", str(inventory), str(playbook)],
            env=os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"},
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"virtiofs mapping ID was invalid\n{output}"

    def run_mapping_verification(entries):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playbook = root / "mapping-verify.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {
                    "_vmfs_compute_nodes": ["compute-3", "compute-2"],
                    "_vmfs_mapping_details": {"results": [{
                        "stdout": json.dumps({"map": entries}),
                        "item": {"id": "d123", "host": "/mnt/media"},
                    }]},
                },
                "tasks": [mapping_verify_task],
            }]))
            inventory = root / "inventory.ini"
            inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
            return subprocess.run(
                [str(ANSIBLE), "-i", str(inventory), str(playbook)],
                env=os.environ | {
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )

    valid_mapping = run_mapping_verification([
        "node=compute-3,path=/mnt/media",
        "node=compute-2,path=/mnt/media/",
    ])
    output = valid_mapping.stdout + valid_mapping.stderr
    assert valid_mapping.returncode == 0, f"valid virtiofs mapping was refused\n{output}"

    conflicting_mapping = run_mapping_verification([
        "node=compute-3,path=/mnt/media",
        "node=compute-2,path=/mnt/other",
    ])
    output = conflicting_mapping.stdout + conflicting_mapping.stderr
    assert conflicting_mapping.returncode != 0, "conflicting virtiofs path was accepted"
    normalized_output = " ".join(output.split())
    assert "different or incomplete compute-node paths" in normalized_output, (
        "conflicting virtiofs path did not report the expected refusal\n" + output
    )


def node(name, status="online"):
    return {"type": "node", "node": name, "status": status}


def config(compute=None, cpu="x86-64-v2-AES", devices=None):
    compute = compute or ["compute-3", "compute-2"]
    proxmox = {
        "node": "localhost",
        "node_roles": {"storage": "storage-1", "compute": compute},
        "storage_ids": {
            "database_vm": "local-mirror",
            "floating_vm": "iscsi-shared",
            "floating_lxc": "nfs-shared",
        },
        "floating_vm_cpu_model": cpu,
    }
    if devices is not None:
        proxmox["devices"] = devices
    return {"proxmox": proxmox}


def run_case(name, placement_class, resources, expected=None, preferred=None,
             cpu="x86-64-v2-AES", expected_error=None, devices=None,
             pci_mapping=None, device=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stub = root / "pvesh"
        stub.write_text(
            f"#!{ANSIBLE.parent / 'python'}\n"
            "import os, sys\n"
            "if any(arg.startswith('/cluster/mapping/pci/') for arg in sys.argv):\n"
            "    print(os.environ['GUEST_PLACEMENT_PCI_MAPPING'])\n"
            "else:\n"
            "    print(os.environ['GUEST_PLACEMENT_RESOURCES'])\n"
        )
        stub.chmod(0o755)

        resolver_vars = {"guest_placement_class": placement_class}
        if preferred is not None:
            resolver_vars["guest_placement_preferred_node"] = preferred
        if device is not None:
            resolver_vars["guest_placement_device"] = device
        tasks = [
            {"ansible.builtin.include_tasks": str(RESOLVER), "vars": resolver_vars}
        ]
        if expected_error:
            tasks.append({
                "ansible.builtin.fail": {"msg": "Expected placement rejection was not raised"}
            })
        else:
            placement = expected
            assertions = [
                f"_guest_placement.node == {json.dumps(placement['node'])}",
                f"_guest_placement.storage == {json.dumps(placement['storage'])}",
                f"_guest_placement.guest_type == {json.dumps(placement['guest_type'])}",
                f"homelabinfra_config.proxmox.node == {json.dumps(placement['node'])}",
            ]
            for key in ("device", "pci_mapping", "pci_id"):
                if key in placement:
                    assertions.append(
                        f"_guest_placement.{key} == {json.dumps(placement[key])}"
                    )
            if placement["guest_type"] == "qemu":
                assertions.append(
                    f"homelabinfra_config.proxmox.vm.storage == {json.dumps(placement['storage'])}"
                )
                if placement["cpu_model"]:
                    assertions.append(
                        "homelabinfra_config.proxmox.vm.cpu == "
                        f"{json.dumps(placement['cpu_model'])}"
                    )
            else:
                assertions.append(
                    "homelabinfra_config.proxmox.lxc.disk_volume.storage == "
                    f"{json.dumps(placement['storage'])}"
                )
            tasks.append({
                "ansible.builtin.assert": {
                    "that": assertions + [
                        f"_guest_placement.cpu_model == {json.dumps(placement['cpu_model'])}"
                    ]
                }
            })
        if expected_error:
            tasks = [{
                "block": tasks,
                "rescue": [{
                    "ansible.builtin.assert": {
                        "that": [
                            f"ansible_failed_result.msg is search({json.dumps(expected_error)})"
                        ]
                    }
                }],
            }]

        playbook = root / "placement.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {"homelabinfra_config": config(cpu=cpu, devices=devices)},
            "environment": {"PATH": str(root) + os.pathsep + os.environ["PATH"]},
            "tasks": tasks,
        }]))
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
            "GUEST_PLACEMENT_RESOURCES": json.dumps(resources),
            "GUEST_PLACEMENT_PCI_MAPPING": json.dumps(pci_mapping or {"map": []}),
        }
        result = subprocess.run(
            [str(ANSIBLE), "-i", str(inventory), str(playbook)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        if expected_error:
            assert result.returncode == 0, f"{name}: expected guarded failure\n{output}"
            assert expected_error in output, f"{name}: missing refusal reason\n{output}"
        else:
            assert result.returncode == 0, f"{name}: placement failed\n{output}"


def main():
    verify_virtiofs_mount_contract()
    run_case(
        "database pinned to storage role",
        "database_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "storage-1", "storage": "local-mirror", "guest_type": "qemu", "cpu_model": ""},
    )
    run_case(
        "floating VM follows online compute preference",
        "floating_vm",
        [node("storage-1"), node("compute-3", "offline"), node("compute-2")],
        {"node": "compute-2", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
    )
    run_case(
        "floating LXC follows online compute preference",
        "floating_lxc",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "nfs-shared", "guest_type": "lxc", "cpu_model": ""},
    )
    run_case(
        "cluster member may request an eligible compute node",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        preferred="compute-2",
    )
    run_case(
        "cluster member cannot request the pinned database node",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        preferred="storage-1",
        expected_error="not an online member of proxmox.node_roles.compute",
    )
    run_case(
        "floating guests refuse an offline compute pool",
        "floating_lxc",
        [node("storage-1"), node("compute-3", "offline"), node("compute-2", "offline")],
        expected_error="None of the ordered nodes in proxmox.node_roles.compute is online",
    )
    run_case(
        "database placement refuses an offline pinned node",
        "database_vm",
        [node("storage-1", "offline"), node("compute-3"), node("compute-2")],
        expected_error="configured storage node is offline",
    )
    gpu_device = {
        "mode": "dedicated",
        "kind": "igpu",
        "node": "compute-3",
        "pci_mapping": "compute-3-igpu",
        "identifiers": ["0000:00:02.0"],
    }
    run_case(
        "GPU VM is pinned to its declared compute node and mapping",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {
            "node": "compute-3",
            "storage": "iscsi-shared",
            "guest_type": "qemu",
            "cpu_model": "x86-64-v2-AES",
            "device": "intel-igpu",
            "pci_mapping": "compute-3-igpu",
            "pci_id": "0000:00:02.0",
        },
        devices={"intel-igpu": gpu_device},
        pci_mapping={"map": [{"node": "compute-3", "id": "0000:00:02.0"}]},
        device="intel-igpu",
    )
    run_case(
        "GPU VM refuses a node outside the compute role",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        expected_error="not in proxmox.node_roles.compute",
        devices={"intel-igpu": {**gpu_device, "node": "storage-1"}},
        pci_mapping={"map": [{"node": "storage-1", "id": "0000:00:02.0"}]},
        device="intel-igpu",
    )
    run_case(
        "GPU VM refuses a mismatched PCI resource mapping",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        expected_error="must map exactly",
        devices={"intel-igpu": gpu_device},
        pci_mapping={"map": [{"node": "compute-3", "id": "0000:00:02.1"}]},
        device="intel-igpu",
    )
    run_case(
        "floating VM refuses host CPU model",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        cpu="host",
        expected_error="floating_vm_cpu_model | lower != 'host'",
    )
    print("Guest placement: class storage, pinned node, ordered floating selection, GPU mapping, virtiofs dependency and safety refusals")


if __name__ == "__main__":
    main()
