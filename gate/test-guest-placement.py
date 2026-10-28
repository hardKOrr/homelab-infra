#!/usr/bin/env python3
"""Exercise the guest placement resolver with a local pvesh fixture."""

import json
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
RESOLVER = REPO / "ansible/tasks/proxmox/resolve-guest-placement.yml"
PCI_MAPPING_PARSER = REPO / "ansible/tasks/proxmox/parse-pci-mapping-entries.yml"
PCI_ATTACH = REPO / "ansible/tasks/proxmox/attach-pci-passthrough.yml"
VM_HOST_MOUNTS = REPO / "ansible/tasks/proxmox/attach-host-mounts-vm.yml"
VM_CLONE = REPO / "ansible/tasks/proxmox/vm-clone.yml"
BOOTSTRAP = REPO / "rundeck/bootstrap-rundeck.sh"


def verify_pci_mapping_contract():
    resolver_tasks = yaml.safe_load(RESOLVER.read_text())
    parser_tasks = yaml.safe_load(PCI_MAPPING_PARSER.read_text())
    attach_tasks = yaml.safe_load(PCI_ATTACH.read_text())
    parser_name = "parse-pci-mapping-entries.yml"

    assert len(parser_tasks) == 2
    assert any(
        task.get("ansible.builtin.include_tasks") == parser_name
        for task in resolver_tasks
    )
    assert any(
        task.get("ansible.builtin.include_tasks") == parser_name
        for task in attach_tasks
    )
    assert "dict(item | split(',') | map('split', '='))" in parser_tasks[1][
        "ansible.builtin.set_fact"
    ]["_proxmox_pci_mapping_entries"]

    placement_check = next(
        task for task in resolver_tasks
        if task.get("name") == "Guest placement | Verify the PCI mapping names this device on its node"
    )
    attach_check = next(
        task for task in attach_tasks
        if task.get("name") == "PCI passthrough | Assert the mapping names this device on this node"
    )
    assert "selectattr('path', 'equalto', _guest_gpu_pci_id)" in placement_check["vars"][
        "_guest_gpu_map_entries"
    ]
    assert "selectattr('path', 'equalto', pci_passthrough_device.id)" in attach_check["vars"][
        "_pci_map_entries"
    ]


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
    index_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Index existing mappings by node and path"
    )
    choose_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Choose each host path's directory mapping"
    )
    extend_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Add new compute nodes to a mapping homelab-infra created"
    )
    refuse_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Refuse an operator mapping that lacks a compute node"
    )
    device_scan_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Resolve existing and missing device slots"
    )
    device_assign_task = next(
        task for task in tasks
        if task.get("name") == "VM host mounts | Assign free virtiofs device slots"
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
                "_vmfs_directory_mappings": [{"id": "d123", "host": "/mnt/media", "exists": False}],
                "_vmfs_compute_nodes": ["compute-3", "compute-2"],
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

    def run_tasks(play_tasks, variables, pvesh_record=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {}
            if pvesh_record is not None:
                stub = root / "pvesh"
                stub.write_text(
                    f"#!{ANSIBLE.parent / 'python'}\n"
                    "import json, os, sys\n"
                    "with open(os.environ['GUEST_MAPPING_ARGV_FILE'], 'a') as out:\n"
                    "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                )
                stub.chmod(0o755)
                environment = {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            playbook = root / "mapping.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "vars": variables | {"homelabinfra_config": {"proxmox": {"node": "localhost"}}},
                "environment": environment,
                "tasks": play_tasks,
            }]))
            inventory = root / "inventory.ini"
            inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
            result = subprocess.run(
                [str(ANSIBLE), "-i", str(inventory), str(playbook)],
                env=os.environ | {
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                    "GUEST_MAPPING_ARGV_FILE": str(pvesh_record or ""),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            return result.returncode, result.stdout + result.stderr

    def detail(mapping_id, entries):
        return {"item": mapping_id, "stdout": json.dumps({"id": mapping_id, "map": entries})}

    three = ["node=compute-1,path=/mnt/media", "node=compute-2,path=/mnt/media/",
             "node=compute-3,path=/mnt/media"]
    old_id = "d" + hashlib.sha1(b"/mnt/media|compute-1|compute-2|compute-3").hexdigest()[:12]
    other_id = "d" + hashlib.sha1(b"/mnt/media|compute-1|compute-2|compute-3|compute-4").hexdigest()[:12]
    new_id = "d" + hashlib.sha1(b"/mnt/media").hexdigest()[:12]

    def choose(details, attached):
        return {
            "_vmfs_compute_nodes": ["compute-1", "compute-2", "compute-3", "compute-4"],
            "_vmfs_host_paths": ["/mnt/media"],
            "_vmfs_directory_mappings": [],
            "_vmfs_existing_mappings": [],
            "_vmfs_existing_device_ids": attached,
            "_vmfs_mapping_details": {"results": details},
        }

    # No mapping yet: the new ID depends on the host path only, so it survives node changes.
    code, output = run_tasks([index_task, choose_task, {"ansible.builtin.assert": {"that": [
        f"_vmfs_directory_mappings == [{{'id': '{new_id}', 'host': '/mnt/media', "
        "'exists': False, 'entries': [], "
        "'missing_nodes': ['compute-1', 'compute-2', 'compute-3', 'compute-4']}]",
    ]}}], choose([], []))
    assert code == 0, f"a new virtiofs mapping did not get a path-only ID\n{output}"

    # A node joined: keep the mapping the VM already mounts and report the new node, even
    # when a mapping created later for the larger node set also exists.
    code, output = run_tasks([index_task, choose_task, {"ansible.builtin.assert": {"that": [
        f"_vmfs_directory_mappings[0].id == '{old_id}'",
        "_vmfs_directory_mappings[0].exists",
        "_vmfs_directory_mappings[0].missing_nodes == ['compute-4']",
    ]}}], choose([
        detail(other_id, three + ["node=compute-4,path=/mnt/media"]),
        detail(old_id, three),
        detail("operator-other", ["node=compute-1,path=/mnt/other"]),
    ], [old_id]))
    assert code == 0, f"the VM's mounted virtiofs mapping was not kept\n{output}"

    with tempfile.TemporaryDirectory() as directory:
        record = Path(directory) / "argv.jsonl"
        code, output = run_tasks([extend_task], {
            "_vmfs_directory_mappings": [{
                "id": old_id, "host": "/mnt/media", "exists": True,
                "entries": three, "missing_nodes": ["compute-4"],
            }],
        }, pvesh_record=record)
        assert code == 0, f"adding a compute node to an owned mapping failed\n{output}"
        assert [json.loads(line) for line in record.read_text().splitlines()] == [[
            "set", f"/cluster/mapping/dir/{old_id}",
            "--map", "node=compute-1,path=/mnt/media",
            "--map", "node=compute-2,path=/mnt/media/",
            "--map", "node=compute-3,path=/mnt/media",
            "--map", "node=compute-4,path=/mnt/media",
        ]], "an owned mapping was not extended with only the missing node"

    code, output = run_tasks([refuse_task], {"_vmfs_directory_mappings": [{
        "id": "operator-media", "host": "/mnt/media", "exists": True,
        "entries": three, "missing_nodes": ["compute-4"],
    }]})
    assert code != 0 and "left unchanged" in " ".join(output.split()), (
        "an operator mapping missing a compute node was accepted\n" + output
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        playbook = root / "device-slots.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "_vmfs_guest_config": {"stdout_lines": [
                    "virtiofs0: dirid=d123,cache=auto",
                    "virtiofs4: d456,cache=auto",
                ]},
                "_vmfs_directory_mappings": [{"id": "d123", "host": "/mnt/media"}],
            },
            "tasks": [device_scan_task, device_assign_task, {
                "ansible.builtin.assert": {
                    "that": [
                        "_vmfs_existing_device_ids == ['d123', 'd456']",
                        "_vmfs_used_device_indexes == [0, 4]",
                        "_vmfs_missing_devices | length == 0",
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
        assert result.returncode == 0, f"existing virtiofs device was not recognized\n{output}"


def verify_vm_clone_existing_node_contract():
    tasks = yaml.safe_load(VM_CLONE.read_text())
    task_names = [task.get("name") for task in tasks]
    resource_lookup = "VM clone | Read cluster resources to preserve an existing guest's node"
    existing_node = "VM clone | Keep an existing guest on its current node"
    resolver = "Resolve the declared placement for this cloned VM"
    assert task_names.index(resource_lookup) < task_names.index(resolver)
    assert task_names.index(existing_node) < task_names.index(resolver)
    resolver_task = next(task for task in tasks if task.get("name") == resolver)
    assert resolver_task["when"] == "_vmc_existing_guest | length == 0"
    assert resolver_task["vars"]["guest_placement_node"] == "{{ proxmox_placement_node | default('') }}"
    node_task = next(task for task in tasks if task.get("name") == existing_node)
    assert "_vmc_existing_guest.node" in node_task["ansible.builtin.set_fact"][
        "homelabinfra_config"
    ]

    def flatten(tasks):
        for task in tasks:
            yield task
            for key in ("block", "rescue", "always"):
                yield from flatten(task.get(key, []))

    stack_tasks = yaml.safe_load(
        (REPO / "ansible/tasks/stack/find-or-create-host.yml").read_text()
    )
    clone_task = next(
        task for task in flatten(stack_tasks)
        if task.get("name") == "Clone the stack host VM"
    )
    assert clone_task["vars"]["proxmox_placement_node"] == "{{ _guest_placement.node }}"

    clone_tasks = yaml.safe_load(VM_CLONE.read_text())
    clone_call = next(
        task for task in clone_tasks
        if task.get("name") == "VM clone | Clone the template"
    )
    assert "_guest_placement.storage" in clone_call["community.proxmox.proxmox_kvm"]["storage"]

    existing_node_helper = yaml.safe_load(
        (REPO / "ansible/tasks/proxmox/preserve-existing-guest-node.yml").read_text()
    )
    assert any(task.get("name") == "Existing guest | Preserve its current node in the guest config"
               for task in existing_node_helper)
    for path, preserve_task_name, resolver_name in (
        (REPO / "ansible/tasks/proxmox/vm-create.yml", "Preserve an existing VM's current node",
         "Resolve the declared placement for this VM"),
        (REPO / "ansible/tasks/proxmox/lxc-create.yml", "Preserve an existing LXC's current node",
         "Resolve the declared placement for this LXC"),
    ):
        tasks = yaml.safe_load(path.read_text())
        names = [task.get("name") for task in tasks]
        assert names.index(preserve_task_name) < names.index(resolver_name)

    for path, storage_task_name in (
        (REPO / "ansible/tasks/proxmox/vm-create.yml", "Apply the selected VM disk storage"),
        (REPO / "ansible/tasks/proxmox/lxc-create.yml", "Apply the selected LXC rootfs storage"),
    ):
        tasks = yaml.safe_load(path.read_text())
        storage_task = next(task for task in tasks if task.get("name") == storage_task_name)
        assert "_guest_placement.storage" in storage_task["ansible.builtin.set_fact"][
            "homelabinfra_instance"
        ]
        module_args_task = next(
            task for task in tasks
            if task.get("name") in (
                "Build VM module arguments from the community.proxmox.proxmox_kvm allowlist",
                "Build LXC module arguments from the community.proxmox.proxmox allowlist",
            )
        )
        module_args_key = "vm_module_args" if "vm_module_args" in module_args_task[
            "ansible.builtin.set_fact"
        ] else "lxc_module_args"
        assert "_guest_placement.node" in module_args_task["ansible.builtin.set_fact"][
            module_args_key
        ]

    resolver_tasks = yaml.safe_load(RESOLVER.read_text())
    for task_name in ("Guest placement | Apply VM target settings",
                      "Guest placement | Apply LXC target settings"):
        task = next(task for task in resolver_tasks if task.get("name") == task_name)
        assert "storage" not in task["ansible.builtin.set_fact"]["homelabinfra_config"]

    template_tasks = yaml.safe_load(
        (REPO / "ansible/tasks/proxmox/ensure-cloud-template.yml").read_text()
    )
    template_placement = next(
        task for task in template_tasks
        if task.get("name") == "Resolve the declared placement before ensuring the VM template"
    )
    assert "guest_placement_additional_storage" in template_placement["vars"]
    assert template_placement["vars"]["guest_placement_node"] == "{{ proxmox_placement_node | default('') }}"
    stack_ensure = next(
        task for task in flatten(stack_tasks)
        if task.get("name") == "Ensure the Debian cloud-init template exists for the stack VM"
    )
    assert stack_ensure["vars"]["proxmox_placement_node"] == "{{ _stack_sizing.node | default('') }}"


def verify_vm_clone_connection_follows_relocated_guest():
    tasks = yaml.safe_load(VM_CLONE.read_text())
    preserve_node = next(
        task for task in tasks
        if task.get("name") == "VM clone | Keep an existing guest on its current node"
    )
    connection = next(
        task for task in tasks
        if task.get("name") == "VM clone | Set connection facts"
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        playbook = root / "connection.yml"
        cases = (
            ("existing VM moved by HA", {"node": "live-node"}, "stale-placement", "live-node"),
            ("new VM uses selected placement", {}, "selected-node", "selected-node"),
        )
        for label, existing_guest, placement_node, expected_node in cases:
            playbook.write_text(
                yaml.safe_dump([{
                    "hosts": "localhost",
                    "gather_facts": False,
                    "vars": {
                        "homelabinfra_config": {"proxmox": {
                            "api_host": "pve.example.invalid",
                            "api_user": "fixture@pve",
                            "api_token_id": "fixture-token",
                            "api_token_secret": "fixture-secret",
                            "node": "configured-node",
                            "vm": {"vmid": 204},
                        }},
                        "_vmc_existing_guest": existing_guest,
                        "_guest_placement": {"node": placement_node},
                    },
                    "tasks": [
                        preserve_node,
                        connection,
                        {"ansible.builtin.assert": {"that": [
                            f"_vmc_api.node == '{expected_node}'",
                        ]}},
                    ],
                }]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(ANSIBLE), "-i", str(inventory), str(playbook)],
                cwd=REPO,
                env=os.environ | {
                    "ANSIBLE_CONFIG": str(REPO / "ansible/ansible.cfg"),
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"{label}: VM clone selected the wrong node\n{output}"


def verify_vm_clone_start_skips_running_guest():
    tasks = yaml.safe_load(VM_CLONE.read_text())
    start = next(
        task for task in tasks
        if task.get("name") == "VM clone | Start the VM"
    )
    start["delegate_to"] = "localhost"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inventory = root / "inventory.ini"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n")
        qm = root / "qm"
        qm.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$QM_MARKER\"\n"
        )
        qm.chmod(0o755)
        playbook = root / "start.yml"
        cases = (
            ("running existing VM", False, "status: running", False),
            ("stopped existing VM", False, "status: stopped", True),
            ("new VM", True, "status: running", True),
        )
        for label, clone_changed, status, should_start in cases:
            marker = root / f"{label.replace(' ', '-')}.marker"
            vars_ = {
                "_vmc_clone": {"changed": clone_changed},
                "_vmc_reused_status": {"stdout": status},
                "_vmc_api": {"node": "localhost"},
                "_vmc_vmid": 204,
            }
            playbook.write_text(
                yaml.safe_dump([{
                    "hosts": "localhost",
                    "gather_facts": False,
                    "vars": vars_,
                    "tasks": [start],
                }]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(ANSIBLE), "-i", str(inventory), str(playbook)],
                cwd=REPO,
                env=os.environ | {
                    "ANSIBLE_CONFIG": str(REPO / "ansible/ansible.cfg"),
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                    "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                    "QM_MARKER": str(marker),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"{label}: VM start task failed\n{output}"
            recap = re.search(r"changed=(\d+)", output)
            assert recap, f"{label}: Ansible recap missing\n{output}"
            expected_changes = 1 if should_start else 0
            assert int(recap.group(1)) == expected_changes, (
                f"{label}: expected changed={expected_changes}\n{output}"
            )
            assert marker.exists() == should_start, f"{label}: unexpected qm start call"
            if should_start:
                assert marker.read_text().strip() == "start 204"


def verify_existing_guest_node_helper():
    helper = REPO / "ansible/tasks/proxmox/preserve-existing-guest-node.yml"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stub = root / "pvesh"
        stub.write_text(
            f"#!{ANSIBLE.parent / 'python'}\n"
            "import json\n"
            "print(json.dumps([{'vmid': 204, 'type': 'qemu', 'node': 'compute-3'}]))\n"
        )
        stub.chmod(0o755)
        playbook = root / "existing-node.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "homelabinfra_config": {"proxmox": {
                    "node": "localhost",
                    "vm": {"node": "old-pin"},
                }},
                "guest_existing_id": 204,
                "guest_existing_type": "qemu",
                "guest_existing_config_key": "vm",
            },
            "environment": {"PATH": str(root) + os.pathsep + os.environ["PATH"]},
            "tasks": [
                {"ansible.builtin.include_tasks": str(helper)},
                {"ansible.builtin.assert": {"that": [
                    "homelabinfra_config.proxmox.node == 'compute-3'",
                    "homelabinfra_config.proxmox.vm.node == 'compute-3'",
                ]}},
            ],
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
        assert result.returncode == 0, f"existing guest node was not preserved\n{output}"


def node(name, status="online"):
    return {"type": "node", "node": name, "status": status}


def config(compute=None, cpu="x86-64-v2-AES", devices=None, explicit_node=None):
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
        "vm": {},
        "lxc": {},
    }
    if explicit_node:
        proxmox["vm"]["node"] = explicit_node
        proxmox["lxc"]["node"] = explicit_node
    if devices is not None:
        proxmox["devices"] = devices
    return {"proxmox": proxmox, "networks": {"default": {"bridge": "vmbr0", "vlan": 20}}}


def default_storage_rows(placement_class, storage, compute_nodes):
    if storage is None:
        storage = {
            "database_vm": "local-mirror",
            "floating_vm": "iscsi-shared",
            "floating_lxc": "nfs-shared",
            "gpu_vm": "iscsi-shared",
        }[placement_class]
    if placement_class == "database_vm":
        storage_nodes = ["storage-1"]
        shared = 0
    else:
        storage_nodes = compute_nodes
        shared = 1
    return [
        {
            "type": "storage",
            "storage": storage,
            "node": name,
            "status": "available",
            "shared": shared,
            "content": "images,rootdir",
        }
        for name in storage_nodes
    ]


def run_case(name, placement_class, resources, expected=None, preferred=None,
             cpu="x86-64-v2-AES", expected_error=None, devices=None,
             pci_mapping=None, device=None, storage=None, storage_resources=None,
             memory=None, networks=None, explicit_node=None, placement_devices=None,
             usb_mapping=None, bridge="vmbr0", compute=None, additional_storage=None,
             stale_vm_storage=None, app_config=None, repeat_placement=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stub = root / "pvesh"
        stub.write_text(
            f"#!{ANSIBLE.parent / 'python'}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "if any(arg.startswith('/cluster/mapping/pci/') for arg in args):\n"
            "    print(os.environ['GUEST_PLACEMENT_PCI_MAPPING'])\n"
            "elif any(arg.startswith('/cluster/mapping/usb/') for arg in args):\n"
            "    print(os.environ['GUEST_PLACEMENT_USB_MAPPING'])\n"
            "elif '/cluster/resources' in args:\n"
            "    kind = args[args.index('--type') + 1]\n"
            "    key = 'GUEST_PLACEMENT_NODES' if kind == 'node' else 'GUEST_PLACEMENT_STORAGES'\n"
            "    print(os.environ[key])\n"
            "elif any(arg.endswith('/status') for arg in args):\n"
            "    path = next(arg for arg in args if arg.startswith('/nodes/'))\n"
            "    node = path.split('/')[2]\n"
            "    print(json.dumps(json.loads(os.environ['GUEST_PLACEMENT_MEMORY'])[node]))\n"
            "elif any(arg.endswith('/network') for arg in args):\n"
            "    path = next(arg for arg in args if arg.startswith('/nodes/'))\n"
            "    node = path.split('/')[2]\n"
            "    print(json.dumps(json.loads(os.environ['GUEST_PLACEMENT_NETWORKS'])[node]))\n"
            "else:\n"
            "    raise SystemExit('unexpected pvesh request')\n"
        )
        stub.chmod(0o755)

        resolver_vars = {"guest_placement_class": placement_class}
        if preferred is not None:
            resolver_vars["guest_placement_preferred_node"] = preferred
        if device is not None:
            resolver_vars["guest_placement_device"] = device
        if storage is not None:
            resolver_vars["guest_placement_storage"] = storage
        if additional_storage is not None:
            resolver_vars["guest_placement_additional_storage"] = additional_storage
        if placement_devices is not None:
            resolver_vars["guest_placement_devices"] = placement_devices
        if bridge is not None:
            resolver_vars["guest_placement_bridge"] = bridge
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
                    f"homelabinfra_config.proxmox.vm.storage == {json.dumps(stale_vm_storage)}"
                    if stale_vm_storage is not None else
                    "homelabinfra_config.proxmox.vm.storage is not defined"
                )
                if explicit_node is None:
                    assertions.append("homelabinfra_config.proxmox.vm.node is not defined")
                if placement["cpu_model"]:
                    assertions.append(
                        "homelabinfra_config.proxmox.vm.cpu == "
                        f"{json.dumps(placement['cpu_model'])}"
                    )
            else:
                assertions.append("homelabinfra_config.proxmox.lxc.disk_volume.storage is not defined")
                if explicit_node is None:
                    assertions.append("homelabinfra_config.proxmox.lxc.node is not defined")
            tasks.append({
                "ansible.builtin.assert": {
                    "that": assertions + [
                        f"_guest_placement.cpu_model == {json.dumps(placement['cpu_model'])}"
                    ]
                }
            })
            if repeat_placement is not None:
                repeat_vars = resolver_vars | {
                    "guest_placement_node": repeat_placement["node"],
                    "guest_placement_storage": repeat_placement["storage"],
                    "guest_placement_use_app_config": False,
                }
                tasks.extend([
                    {
                        "ansible.builtin.include_tasks": str(RESOLVER),
                        "vars": repeat_vars,
                    },
                    {
                        "ansible.builtin.assert": {
                            "that": [
                                f"_guest_placement.node == {json.dumps(repeat_placement['node'])}",
                                "_guest_placement_memory_candidates | length == 1",
                                "_guest_placement_memory_candidates[0].node == "
                                f"{json.dumps(repeat_placement['node'])}",
                                "_guest_placement_app_storage == ''",
                                "_guest_placement_app_node == ''",
                            ]
                        }
                    },
                ])
        if expected_error:
            tasks = [{
                "block": tasks,
                "rescue": [{
                    "ansible.builtin.assert": {
                        "that": [
                            f"ansible_failed_result | to_json is search({json.dumps(expected_error)})"
                        ]
                    }
                }],
            }]

        playbook = root / "placement.yml"
        compute_nodes = config(
            compute=compute, cpu=cpu, devices=devices, explicit_node=explicit_node
        )["proxmox"]["node_roles"]["compute"]
        node_memory = {
            "compute-3": {"memory": {"available": 4 * 1024**3, "total": 16 * 1024**3, "used": 12 * 1024**3}},
            "compute-2": {"memory": {"available": 8 * 1024**3, "total": 16 * 1024**3, "used": 8 * 1024**3}},
            "storage-1": {"memory": {"available": 12 * 1024**3, "total": 16 * 1024**3, "used": 4 * 1024**3}},
        }
        if memory:
            node_memory.update(memory)
        network_rows = {
            item["node"]: [{
                "type": "bridge", "iface": "vmbr0", "active": 1,
                "bridge_vlan_aware": 1, "bridge_vids": "10 20 30",
            }]
            for item in resources
        }
        if networks:
            network_rows.update(networks)
        test_config = config(
            compute=compute, cpu=cpu, devices=devices, explicit_node=explicit_node
        )
        if stale_vm_storage is not None:
            test_config["proxmox"]["vm"]["storage"] = stale_vm_storage
        authored_proxmox = dict(test_config["proxmox"])
        authored_proxmox["vm"] = dict(test_config["proxmox"]["vm"])
        authored_proxmox["vm"].pop("storage", None)
        selected_storage = storage or {
            "database_vm": "local-mirror",
            "floating_vm": "iscsi-shared",
            "floating_lxc": "nfs-shared",
            "gpu_vm": "iscsi-shared",
        }[placement_class]
        storage_rows = storage_resources or default_storage_rows(
            placement_class, selected_storage, compute_nodes
        )
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "homelabinfra_config": test_config,
                "_config_proxmox": {"proxmox": authored_proxmox},
                **({"app_config": app_config} if app_config is not None else {}),
            },
            "environment": {"PATH": str(root) + os.pathsep + os.environ["PATH"]},
            "tasks": tasks,
        }]))
        inventory = root / "inventory.ini"
        inventory.write_text(
            "[local]\n"
            "localhost ansible_connection=local\n"
            "compute-3 ansible_connection=local\n"
            "compute-2 ansible_connection=local\n"
            "storage-1 ansible_connection=local\n"
        )
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
            "GUEST_PLACEMENT_NODES": json.dumps(resources),
            "GUEST_PLACEMENT_STORAGES": json.dumps(storage_rows),
            "GUEST_PLACEMENT_PCI_MAPPING": json.dumps(pci_mapping or {"map": []}),
            "GUEST_PLACEMENT_USB_MAPPING": json.dumps(usb_mapping or {"map": []}),
            "GUEST_PLACEMENT_MEMORY": json.dumps(node_memory),
            "GUEST_PLACEMENT_NETWORKS": json.dumps(network_rows),
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
    verify_pci_mapping_contract()
    verify_virtiofs_mount_contract()
    verify_vm_clone_existing_node_contract()
    verify_vm_clone_connection_follows_relocated_guest()
    verify_vm_clone_start_skips_running_guest()
    verify_existing_guest_node_helper()
    run_case(
        "database pinned to storage role",
        "database_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "storage-1", "storage": "local-mirror", "guest_type": "qemu", "cpu_model": ""},
    )
    run_case(
        "floating VM selects an online compute node",
        "floating_vm",
        [node("storage-1"), node("compute-3", "offline"), node("compute-2")],
        {"node": "compute-2", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
    )
    run_case(
        "available-memory tie keeps configured compute order",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        memory={
            name: {"memory": {"available": 4 * 1024**3, "total": 16 * 1024**3, "used": 12 * 1024**3}}
            for name in ("compute-3", "compute-2")
        },
    )
    run_case(
        "stale runtime VM storage does not override the guest class",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        stale_vm_storage="local-zfs",
    )
    run_case(
        "floating LXC selects the compute node with most available memory",
        "floating_lxc",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "nfs-shared", "guest_type": "lxc", "cpu_model": ""},
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
        expected_error="not available for LXC root filesystems",
    )
    run_case(
        "database placement refuses an offline pinned node",
        "database_vm",
        [node("storage-1", "offline"), node("compute-3"), node("compute-2")],
        expected_error="not available for VM images",
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
        pci_mapping={"map": [
            "node=compute-3,path=0000:00:02.0,id=8086:1234",
            "node=compute-2,path=0000:00:02.1,id=8086:1234",
        ]},
        device="intel-igpu",
    )
    run_case(
        "GPU VM's device pin may target a node outside the unpinned compute pool",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {
            "node": "storage-1",
            "storage": "iscsi-shared",
            "guest_type": "qemu",
            "cpu_model": "x86-64-v2-AES",
            "device": "intel-igpu",
            "pci_mapping": "compute-3-igpu",
            "pci_id": "0000:00:02.0",
        },
        devices={"intel-igpu": {**gpu_device, "node": "storage-1"}},
        pci_mapping={"map": ["node=storage-1,path=0000:00:02.0,id=8086:1234"]},
        device="intel-igpu",
        storage_resources=[{
            "type": "storage", "storage": "iscsi-shared", "node": "storage-1",
            "status": "available", "shared": 1, "content": "images",
        }],
    )
    run_case(
        "GPU VM refuses a mismatched PCI resource mapping",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        expected_error="must map exactly",
        devices={"intel-igpu": gpu_device},
        pci_mapping={"map": ["node=compute-3,path=0000:00:02.1,id=8086:1234"]},
        device="intel-igpu",
    )
    run_case(
        "GPU VM refuses a matching PCI path mapped to another node",
        "gpu_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        expected_error="must map exactly",
        devices={"intel-igpu": gpu_device},
        pci_mapping={"map": ["node=compute-2,path=0000:00:02.0,id=8086:1234"]},
        device="intel-igpu",
    )
    run_case(
        "free-memory ranking overrides compute-list order",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        compute=["compute-2", "compute-3"],
        memory={
            "compute-2": {"memory": {"available": 3 * 1024**3, "total": 16 * 1024**3, "used": 13 * 1024**3}},
            "compute-3": {"memory": {"available": 9 * 1024**3, "total": 16 * 1024**3, "used": 7 * 1024**3}},
        },
    )
    run_case(
        "explicit per-guest node wins over memory ranking",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        explicit_node="compute-3",
    )
    run_case(
        "app-level VM node pins a new guest",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        app_config={"proxmox": {"node": "compute-3"}},
    )
    run_case(
        "repeated placement resets candidate and optional app inputs",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "local-zfs", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        app_config={"proxmox": {"vm": {"node": "compute-3", "storage": "local-zfs"}}},
        storage_resources=[
            {"type": "storage", "storage": "local-zfs", "node": "compute-3",
             "status": "available", "shared": 0, "content": "images"},
            *[
                {"type": "storage", "storage": "iscsi-shared", "node": name,
                 "status": "available", "shared": 1, "content": "images"}
                for name in ("compute-3", "compute-2")
            ],
        ],
        memory={
            "compute-3": {"memory": {"available": 12 * 1024**3, "total": 16 * 1024**3, "used": 4 * 1024**3}},
            "compute-2": {"memory": {"available": 8 * 1024**3, "total": 16 * 1024**3, "used": 8 * 1024**3}},
        },
        repeat_placement={"node": "compute-2", "storage": "iscsi-shared"},
    )
    run_case(
        "flat LXC app storage selects and pins its local node",
        "floating_lxc",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "local-zfs", "guest_type": "lxc", "cpu_model": ""},
        app_config={"proxmox": {"disk_volume": {"storage": "local-zfs"}}},
        storage_resources=[{
            "type": "storage", "storage": "local-zfs", "node": "compute-2",
            "status": "available", "shared": 0, "content": "rootdir",
        }],
    )
    run_case(
        "single-node local storage pins outside the compute pool",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "storage-1", "storage": "local-zfs", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        storage="local-zfs",
        storage_resources=[{
            "type": "storage", "storage": "local-zfs", "node": "storage-1",
            "status": "available", "shared": 0, "content": "images,rootdir",
        }],
    )
    run_case(
        "template placement intersects its storage with the target guest storage",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "template-store", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        storage="template-store",
        additional_storage="local-zfs",
        storage_resources=[
            {"type": "storage", "storage": "template-store", "node": name,
             "status": "available", "shared": 1, "content": "images"}
            for name in ("compute-3", "compute-2")
        ] + [
            {"type": "storage", "storage": "local-zfs", "node": "compute-2",
             "status": "available", "shared": 0, "content": "images"}
        ],
    )
    run_case(
        "conflicting local template and guest stores refuse placement",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        expected_error="point to different",
        storage="template-store",
        additional_storage="local-zfs",
        storage_resources=[
            {"type": "storage", "storage": "template-store", "node": "compute-3",
             "status": "available", "shared": 0, "content": "images"},
            {"type": "storage", "storage": "local-zfs", "node": "compute-2",
             "status": "available", "shared": 0, "content": "images"},
        ],
    )
    run_case(
        "multi-node local storage is restricted to nodes exposing that pool",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "local-zfs", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        storage="local-zfs",
        storage_resources=[
            {"type": "storage", "storage": "local-zfs", "node": name,
             "status": "available", "shared": 0, "content": "images,rootdir"}
            for name in ("compute-2", "compute-3")
        ],
    )
    run_case(
        "inactive bridge excludes a higher-memory candidate",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        networks={
            "compute-2": [{"type": "bridge", "iface": "vmbr0", "active": 0,
                            "bridge_vlan_aware": 1, "bridge_vids": "20"}],
            "compute-3": [{"type": "bridge", "iface": "vmbr0", "active": 1,
                            "bridge_vlan_aware": 1, "bridge_vids": "20"}],
        },
    )
    run_case(
        "bridge VLAN membership excludes an unreachable network",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        networks={
            "compute-2": [{"type": "bridge", "iface": "vmbr0", "active": 1,
                            "bridge_vlan_aware": 1, "bridge_vids": "10 30"}],
            "compute-3": [{"type": "bridge", "iface": "vmbr0", "active": 1,
                            "bridge_vlan_aware": 1, "bridge_vids": "10-25"}],
        },
    )
    run_case(
        "VLAN-aware bridge without bridge_vids allows the default VLAN range",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-2", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        networks={
            "compute-3": [{"type": "bridge", "iface": "vmbr0", "active": 1,
                            "bridge_vlan_aware": 1}],
            "compute-2": [{"type": "bridge", "iface": "vmbr0", "active": 1,
                            "bridge_vlan_aware": 1}],
        },
    )
    run_case(
        "USB mapping on one node pins the guest there",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        {"node": "compute-3", "storage": "iscsi-shared", "guest_type": "qemu", "cpu_model": "x86-64-v2-AES"},
        devices={"zigbee": {"mode": "dedicated", "kind": "usb", "identifiers": ["zigbee"]}},
        placement_devices=["zigbee"],
        usb_mapping={"map": [{"node": "compute-3", "path": "1-2"}]},
    )
    run_case(
        "conflicting explicit and local-storage pins refuse creation",
        "floating_vm",
        [node("storage-1"), node("compute-3"), node("compute-2")],
        expected_error="point to different",
        explicit_node="compute-3",
        storage="local-zfs",
        storage_resources=[{
            "type": "storage", "storage": "local-zfs", "node": "storage-1",
            "status": "available", "shared": 0, "content": "images,rootdir",
        }],
    )
    run_case(
        "floating VM refuses host CPU model",
        "floating_vm",
        [node("compute-3"), node("compute-2")],
        cpu="host",
        expected_error="floating_vm_cpu_model | lower != 'host'",
    )
    print("Guest placement: storage/network filtering, available-memory ranking, explicit/storage/device pins, GPU mapping, virtiofs dependency and safety refusals")


if __name__ == "__main__":
    main()
