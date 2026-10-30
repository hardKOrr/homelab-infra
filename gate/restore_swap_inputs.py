"""Read the inputs a role restore passes to the shared restore swap.

gate/test-restore-swap.py proves ansible/tasks/recovery/restore-swap.yml itself (preview,
staging, stop, move aside, ownership, start, rollback). An application test checks what
its role supplies: paths, owner, mode, services, health URL and hooks.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"


def swap_inputs(role, task_file="restore.yml"):
    """Return the vars of the role's single include of the shared restore swap."""
    tasks = yaml.safe_load((repo / "ansible/roles" / role / "tasks" / task_file).read_text())
    includes = [task for task in tasks
                if str(task.get("ansible.builtin.include_tasks", "")).endswith(
                    "tasks/recovery/restore-swap.yml")]
    assert len(includes) == 1, f"{role}/{task_file} must include restore-swap.yml once"
    assert "when" not in includes[0], f"{role}: the swap runs on every restore, preview included"
    variables = includes[0]["vars"]
    for hook in ("staged", "stopped", "replaced", "verified", "rollback"):
        path = variables.get(f"recovery_swap_{hook}_hook")
        if path:
            assert path.startswith("{{ role_path }}/tasks/"), path
            assert (repo / "ansible/roles" / role / path.removeprefix("{{ role_path }}/")).exists(), path
    return variables


def run_hook(role, hook, variables, stub=None):
    """Run the role's restore-<hook>.yml on localhost; stub(tasks) may replace boundaries."""
    tasks = yaml.safe_load((repo / "ansible/roles" / role / "tasks" / f"restore-{hook}.yml").read_text())
    if stub:
        tasks = stub(tasks)
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "hook.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": tasks}]))
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                              env=os.environ | {"ANSIBLE_NOCOLOR": "1"}, text=True,
                              capture_output=True, check=False)
