#!/usr/bin/env python3
"""Check Navidrome Restore plan output for list and selected-point modes."""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
source = yaml.safe_load((repo / "ansible/roles/navidrome/tasks/restore.yml").read_text())
report_tasks = [
    task for task in source
    if task.get("name", "").startswith("Navidrome restore | Report ")
]


def run_report(snapshot, snapshots_output):
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "restore-plan.yml"
        tasks = copy.deepcopy(report_tasks)
        tasks[0]["register"] = "_selected_plan_report"
        tasks[1]["register"] = "_available_plan_report"
        if snapshot:
            expected = [
                "_selected_plan_report.msg == 'No data was changed. Selected recovery point: ' ~ restore_snapshot",
                "_available_plan_report.skipped | default(false) | bool",
            ]
        else:
            expected = [
                "_selected_plan_report.skipped | default(false) | bool",
                "'snapshot-list-output' in _available_plan_report.msg",
            ]
        tasks.append({"ansible.builtin.assert": {"that": expected}})
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "restore_overwrite": False,
                "restore_snapshot": snapshot,
                "_navidrome_restore_backup_id": "navidrome",
                "_navidrome_restore_snapshots": {"stdout": snapshots_output},
            },
            "tasks": tasks,
        }]))
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
            env=os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def main():
    assert len(report_tasks) == 2
    selected = "host/navidrome/fixture-recovery-point"
    run_report(selected, "")
    run_report("", "snapshot-list-output")

    print("Navidrome Restore: selected-point and snapshot-list plan output passed")


if __name__ == "__main__":
    main()
