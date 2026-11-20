#!/usr/bin/env python3
"""Run tasks/stack/report-undersized-host.yml against hosts of several sizes.

Checks: a smaller host warns and tries to notify; a host at or above its sizing, or one
the inventory gives no size for, reports nothing; the deploy continues either way.
"""
import subprocess, tempfile
from pathlib import Path
import yaml

R = Path(__file__).resolve().parent.parent
TASKS = R / "ansible/tasks/stack/report-undersized-host.yml"
SIZING = {"cores": 4, "memory": 8192}
CASES = {
    "small-memory": ({"proxmox_memory": 2048, "proxmox_cores": 4}, ["memory 2048 MiB < 8192 MiB"]),
    "small-both": ({"proxmox_memory": 4096, "proxmox_cores": 2}, ["memory 4096 MiB < 8192 MiB", "cores 2 < 4"]),
    "sized": ({"proxmox_memory": 8192, "proxmox_cores": 4}, []),
    "larger": ({"proxmox_memory": 16384, "proxmox_cores": 8}, []),
    "unknown": ({}, []),
}

with tempfile.TemporaryDirectory() as t:
    for case, (facts, expected) in CASES.items():
        play = [{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"report_stack_host": "stack-productivity", "report_stack_sizing": SIZING,
                     "homelabinfra_infra": {}},
            "tasks": [
                {"ansible.builtin.add_host": dict(name="stack-productivity", **facts)},
                {"ansible.builtin.include_tasks": str(TASKS)},
                {"ansible.builtin.assert": {"that": [f"_stack_undersized == {expected!r}"]}},
            ],
        }]
        path = Path(t) / f"{case}.yml"
        path.write_text(yaml.safe_dump(play))
        p = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(path)],
                           capture_output=True, text=True, cwd=R / "ansible")
        assert p.returncode == 0, f"{case}\n{p.stdout}\n{p.stderr}"
        assert ("WARNING: stack host stack-productivity" in p.stdout) == bool(expected), f"{case}\n{p.stdout}"
print("undersized stack host report: ok")
