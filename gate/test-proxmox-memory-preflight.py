#!/usr/bin/env python3
"""Check shared memory preflight coverage and placement before Proxmox mutations."""

import json
from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml
import task_parts

repo = Path(__file__).resolve().parents[1]
task_dir = repo / "ansible/tasks/proxmox"
preflight = yaml.safe_load((task_dir / "check-node-free-memory.yml").read_text())


def named_tasks(filename):
    return task_parts.inline(task_dir / filename)


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
config = task_index(
    ensure,
    name="Ensure running | Read the guest config needed for memory checks or balloon convergence",
)
decode = task_index(ensure, name="Ensure running | Decode current guest config for balloon convergence")
balloon_memory = task_index(ensure, name="Ensure running | Resolve the configured VM memory maximum")
balloon_validation = task_index(ensure, name="Ensure running | Validate the configured balloon minimum")
balloon_converge = task_index(ensure, name="Ensure running | Converge the configured VM balloon minimum")
assert config < decode < balloon_memory < balloon_validation < balloon_converge < memory_check < start
assert ensure[config]["when"] == [
    "_egr_status != 'running' or (_egr_vmtype == 'qemu' and (_egr_balloon_requested | string | length > 0))"
]
assert ensure[memory_check]["when"] == "_egr_status != 'running'"
memory_expression = ensure[memory_check]["vars"]["proxmox_guest_memory_mib"]
assert "_egr_balloon_memory_mib" in memory_expression
assert "_egr_balloon_requested" in memory_expression
assert "_egr_guest_config.stdout | from_json" in memory_expression

jinja = Environment(undefined=StrictUndefined)
jinja.filters["from_json"] = json.loads
memory_template = jinja.from_string(memory_expression)
configured_memory = memory_template.render(
    _egr_balloon_requested="512",
    _egr_balloon_memory_mib="4096",
    _egr_guest_config={"stdout": json.dumps({"memory": 2048})},
)
assert configured_memory == "4096", configured_memory
current_memory = memory_template.render(
    _egr_balloon_requested="",
    _egr_guest_config={"stdout": json.dumps({"memory": 2048})},
)
assert current_memory == "2048", current_memory

print("Proxmox memory preflight covers create/start paths and skips running guests")
