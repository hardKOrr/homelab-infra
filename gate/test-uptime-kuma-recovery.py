#!/usr/bin/env python3
"""Guard Uptime Kuma's native PBS backup and in-place recovery contract."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BACKUP = yaml.safe_load((ROOT / "ansible/roles/uptime-kuma/tasks/backup.yml").read_text())
RESTORE = yaml.safe_load((ROOT / "ansible/roles/uptime-kuma/tasks/restore.yml").read_text())


def tasks_recursive(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks_recursive(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks_recursive(value.get(key, []))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def check_contract_and_backup():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/uptime-kuma.yml").read_text()
    )["uptime_kuma_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2

    upload = task_named(
        list(tasks_recursive(BACKUP)),
        "Uptime Kuma backup | Quiesce Uptime Kuma and archive its data directory to PBS",
    )
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    shared = upload["vars"]
    assert shared["pbs_client_runtime"] == "docker"
    assert shared["pbs_client_stop_services"] == ["uptime-kuma"]
    assert [a["name"] for a in shared["pbs_client_archives"]] == ["data.pxar"]


def check_restore_contract_and_rollback():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/uptime-kuma.yml").read_text()
    )["uptime_kuma_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]

    restore_tasks = list(tasks_recursive(RESTORE))
    prepoint_guard = task_named(
        restore_tasks, "Uptime Kuma restore | Require an independent pre-restore point"
    )
    assert prepoint_guard["when"] == [
        "restore_destination | default('existing') == 'existing'", "restore_overwrite | bool"
    ]
    assert "_kuma_restore_target_backup_id" in str(prepoint_guard["ansible.builtin.assert"]["that"])
    assert "restore_pre_restore_point != _kuma_restore_snapshot" in prepoint_guard[
        "ansible.builtin.assert"
    ]["that"]

    transaction = task_named(
        restore_tasks, "Uptime Kuma restore | Replace and verify the application data"
    )
    block = transaction["block"]
    names = [task.get("name") for task in block]
    stop = task_named(block, "Uptime Kuma restore | Stop only the Uptime Kuma service")
    assert stop["community.docker.docker_compose_v2"]["state"] == "stopped"
    assert stop["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]
    validate = task_named(
        block, "Uptime Kuma restore | Validate both PBS points and extract the selected one"
    )
    assert validate["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert validate["vars"]["pbs_client_pre_restore_backup_id"] == "{{ _kuma_restore_target_backup_id }}"
    assert task_named(block, "Uptime Kuma restore | Require the staged database")
    compose_tasks = [task for task in restore_tasks if "community.docker.docker_compose_v2" in task]
    assert compose_tasks
    for task in compose_tasks:
        assert task["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]

    for before, after in [
        ("Uptime Kuma restore | Validate both PBS points and extract the selected one",
         "Uptime Kuma restore | Require the staged database"),
        ("Uptime Kuma restore | Require the staged database",
         "Uptime Kuma restore | Move the live data aside for rollback"),
        ("Uptime Kuma restore | Move the live data aside for rollback",
         "Uptime Kuma restore | Install the validated data directory"),
        ("Uptime Kuma restore | Install the validated data directory",
         "Uptime Kuma restore | Start only the Uptime Kuma service"),
        ("Uptime Kuma restore | Start only the Uptime Kuma service",
         "Uptime Kuma restore | Wait for the initialized server"),
        ("Uptime Kuma restore | Wait for the initialized server",
         "Uptime Kuma restore | Mark the application restore complete"),
    ]:
        assert names.index(before) < names.index(after), (before, after)

    rescue_names = [task.get("name") for task in transaction["rescue"]]
    expected_rescue_order = [
        "Uptime Kuma restore | Stop the failed replacement",
        "Uptime Kuma restore | Remove only the incomplete replacement",
        "Uptime Kuma restore | Put the original data directory back",
        "Uptime Kuma restore | Restart the original service after rollback",
        "Uptime Kuma restore | Record whether rollback completed",
        "Uptime Kuma restore | Fail with the rollback result",
    ]
    assert rescue_names == expected_rescue_order
    always = transaction["always"][-1]
    assert always["name"] == "Uptime Kuma restore | Remove staging after success or completed rollback"
    assert "_kuma_restore_rollback_complete" in str(always["when"])


def main():
    check_contract_and_backup()
    check_restore_contract_and_rollback()
    print("Uptime Kuma: shared PBS client use, destination guards and rollback order passed")


if __name__ == "__main__":
    main()
