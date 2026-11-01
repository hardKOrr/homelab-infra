#!/usr/bin/env python3
"""Guard Uptime Kuma's PBS backup and its inputs to the shared restore swap.

Checks: quiesced PBS snapshots, destination guards, and the restore swap inputs with the
`kuma.db` and initialized-server hooks.
"""

from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

ROOT = Path(__file__).resolve().parents[1]
BACKUP = yaml.safe_load((ROOT / "ansible/roles/uptime-kuma/tasks/backup.yml").read_text())
RESTORE = yaml.safe_load(
    (ROOT / "ansible/roles/uptime-kuma/tasks/restore-docker.yml").read_text()
)


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
        BACKUP, "Uptime Kuma backup | Quiesce Uptime Kuma and archive its data directory to PBS"
    )
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    shared = upload["vars"]
    assert shared["pbs_client_runtime"] == "docker"
    assert shared["pbs_client_stop_services"] == ["uptime-kuma"]
    assert [a["name"] for a in shared["pbs_client_archives"]] == ["data.pxar"]


def check_restore():
    prepoint_guard = task_named(
        RESTORE, "Uptime Kuma restore | Require an independent pre-restore point"
    )
    assert prepoint_guard["when"] == [
        "restore_destination | default('existing') == 'existing'", "restore_overwrite | bool"
    ]
    assert "restore_pre_restore_point != _kuma_restore_snapshot" in prepoint_guard[
        "ansible.builtin.assert"
    ]["that"]

    swap = swap_inputs("uptime-kuma", "restore-docker.yml")
    assert swap["recovery_swap_runtime"] == "docker"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_stop_services"] == ["uptime-kuma"]
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _kuma_restore_target_backup_id }}"
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.data_path }}", "owner": 0, "group": 0, "mode": "0750"}
    staged = (ROOT / "ansible/roles/uptime-kuma/tasks/restore-staged.yml").read_text()
    assert "{{ recovery_swap_stage }}/data/kuma.db" in staged
    verified = (ROOT / "ansible/roles/uptime-kuma/tasks/restore-verified.yml").read_text()
    assert "== 'entryPage'" in verified


def main():
    check_contract_and_backup()
    check_restore()
    print("Uptime Kuma: shared PBS client use, destination guards and restore swap inputs passed")


if __name__ == "__main__":
    main()
