#!/usr/bin/env python3
"""Check Navidrome Restore plan output for list and selected-point modes."""

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
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "restore_overwrite": False,
                "restore_snapshot": snapshot,
                "_navidrome_restore_backup_id": "navidrome",
                "_navidrome_restore_snapshots": {"stdout": snapshots_output},
            },
            "tasks": report_tasks,
        }]))
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
            env=os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout


def main():
    assert len(report_tasks) == 2
    selected = "host/navidrome/fixture-recovery-point"
    selected_output = run_report(selected, "")
    assert f"Selected recovery point: {selected}" in selected_output
    assert "provide snapshot=<id>" not in selected_output

    listed_output = run_report("", "snapshot-list-output")
    assert "PBS snapshots for host/navidrome:" in listed_output
    assert "snapshot-list-output" in listed_output

    print("Navidrome Restore: selected-point and snapshot-list plan output passed")


if __name__ == "__main__":
    main()
