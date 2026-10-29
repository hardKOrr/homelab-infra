#!/usr/bin/env python3
"""Check Open WebUI restore extracts and checks both points before it stops the service."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def open_webui_restore_checks_both_points_before_stopping():
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/open-webui/tasks/restore.yml").read_text(
            encoding="utf-8"
        )
    )
    block = task_named(tasks, "Open WebUI restore | Replace application state")["block"]
    names = [task["name"] for task in block]

    staging = task_named(block, "Open WebUI restore | Create private staging space")
    assert staging["ansible.builtin.tempfile"]["path"] == "/var/tmp", "staging must stay outside /tmp"

    b_point = task_named(block, "Open WebUI restore | Extract the independent existing-target B point")
    a_point = task_named(block, "Open WebUI restore | Extract selected A")
    for extract in (b_point, a_point):
        assert extract["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
        assert extract["vars"]["pbs_client_runtime"] == "docker"
        assert [archive["name"] for archive in extract["vars"]["pbs_client_archives"]] == ["data.pxar"]
    assert b_point["vars"]["pbs_client_snapshot"] == "{{ restore_pre_restore_point }}"
    assert b_point["vars"]["pbs_client_backup_id"] == "{{ _open_webui_target_backup_id }}"

    check = task_named(block, "Open WebUI restore | Check webui.db in each extracted point")
    assert "PRAGMA integrity_check" in check["ansible.builtin.command"]["argv"][-1]
    assert "pre-restore" in check["loop"] and "selected" in check["loop"]

    stop = names.index("Open WebUI restore | Stop only Open WebUI after both points verify")
    for task in (b_point, a_point, check):
        assert names.index(task["name"]) < stop, f"{task['name']} must run before the stop"


if __name__ == "__main__":
    open_webui_restore_checks_both_points_before_stopping()
    print("ok Open WebUI restore extracts and checks both points before stopping")
