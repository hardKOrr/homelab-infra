#!/usr/bin/env python3
"""Check shared memory preflight coverage and placement before Proxmox mutations."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
task_dir = repo / "ansible/tasks/proxmox"
preflight = yaml.safe_load((task_dir / "check-node-free-memory.yml").read_text())


def named_tasks(filename):
    return yaml.safe_load((task_dir / filename).read_text())


def task_index(tasks, *, name=None, include=None):
    for index, task in enumerate(tasks):
        if name is not None and task.get("name") == name:
            return index
        if include is not None and task.get("ansible.builtin.include_tasks") == include:
            return index
    raise AssertionError(f"task not found: {name or include}")


status_read = preflight[1]["ansible.builtin.command"]["argv"]
assert status_read == ["pvesh", "get", "/nodes/{{ proxmox_memory_node }}/status", "--output-format", "json"]
capacity_assert = preflight[2]["ansible.builtin.assert"]
memory_vars = str(preflight[2]["vars"])
assert "_proxmox_memory_available_bytes | int" in str(capacity_assert["that"])
assert "_proxmox_memory_needed_mib | int" in str(capacity_assert["that"])
assert "memory.available" in memory_vars
assert "memory.total" in memory_vars and "memory.used" in memory_vars
assert "memory.free" not in memory_vars
assert "proxmox_memory_guest" in capacity_assert["fail_msg"]
assert "proxmox_memory_node" in capacity_assert["fail_msg"]
assert "_proxmox_memory_needed_mib" in capacity_assert["fail_msg"]
assert "_proxmox_memory_available_mib" in capacity_assert["fail_msg"]

expected = {
    "vm-create.yml": ("Create VM", "Start the VM"),
    "vm-clone.yml": ("VM clone | Clone the template", "VM clone | Start the VM"),
    "lxc-create.yml": ("Create LXC container", "Start the LXC container"),
}
for filename, operation_names in expected.items():
    tasks = named_tasks(filename)
    includes = [
        index for index, task in enumerate(tasks)
        if task.get("ansible.builtin.include_tasks") == "check-node-free-memory.yml"
    ]
    operations = [task_index(tasks, name=name) for name in operation_names]
    assert len(includes) == 2, (filename, includes)
    assert includes[0] < operations[0] and includes[1] < operations[1], (filename, includes, operations)

ensure = named_tasks("ensure-guest-running.yml")
memory_check = task_index(ensure, include="check-node-free-memory.yml")
start = task_index(ensure, name="Ensure running | Start the guest if it is not running")
config = task_index(ensure, name="Ensure running | Read the stopped guest's configured memory")
assert config < memory_check < start
assert ensure[memory_check]["when"] == "_egr_status != 'running'"
assert "_egr_guest_config.stdout | from_json" in str(ensure[memory_check]["vars"])

print("Proxmox memory preflight covers create/start paths and skips running guests")
