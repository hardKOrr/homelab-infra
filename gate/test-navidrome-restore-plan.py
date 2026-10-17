#!/usr/bin/env python3
"""Check Navidrome Restore plans and ownership-safe archive extraction."""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
source = yaml.safe_load((repo / "ansible/roles/navidrome/tasks/restore.yml").read_text())


def tasks_recursive(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            if section in task:
                yield from tasks_recursive(task[section])


all_tasks = list(tasks_recursive(source))
report_tasks = [
    task for task in source
    if task.get("name", "").startswith("Navidrome restore | Report ")
]
extract_task = next(
    task for task in all_tasks
    if task.get("name") == "Navidrome restore | Extract the selected archive"
)
extract_script = " ".join(extract_task["ansible.builtin.command"]["argv"][-1].split())
extract_script = extract_script.replace(" \\ ", " ")
assert (
    "proxmox-backup-client restore --allow-existing-dirs true" in extract_script
)
assert '--ignore-ownership true "$SNAPSHOT" data.pxar /restore' in extract_script
assert "--allow-existing-dirs true" in extract_script
assert "--ignore-ownership true" in extract_script
assert extract_task["no_log"] is True
assert extract_task["register"] == "_navidrome_restore_extract"
diagnostic_task = next(
    task for task in all_tasks
    if task.get("name") == "Navidrome restore | Report safe archive extraction diagnostics"
)
diagnostic_message = " ".join(diagnostic_task["ansible.builtin.debug"]["msg"])
assert "sanitized_stderr=" in diagnostic_message
assert "regex_replace" in diagnostic_message
ownership_task = next(
    task for task in all_tasks
    if task.get("name") == "Navidrome restore | Set data ownership for the container user"
)
assert all_tasks.index(ownership_task) > all_tasks.index(extract_task)
assert ownership_task["ansible.builtin.file"]["recurse"] is True


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

    print("Navidrome Restore: plan output and ownership-safe archive extraction passed")


if __name__ == "__main__":
    main()
