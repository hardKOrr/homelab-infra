#!/usr/bin/env python3
"""Check Ntfy's PBS recovery contract and its inputs to the shared restore swap.

Checks: native auth/cache archives and the two paths, owner and auth-database checks Ntfy
gives the restore swap.
"""

from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs
from remove_plan import load_playbook


ROOT = Path(__file__).resolve().parents[1]
BACKUP = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/backup.yml").read_text())


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


def check_contract():
    defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/ntfy.yml").read_text())["ntfy_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2

    main = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/main.yml").read_text())
    install = task_named(main, "Install the pinned PBS client when this lab declares app backups")
    assert install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-install.yml")
    assert "backups" in str(install["when"])

    backup_tasks = list(tasks_recursive(BACKUP))
    upload = task_named(backup_tasks, "Ntfy backup | Quiesce Ntfy and archive auth and cache state to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "native"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ _ntfy_backup_service_name }}"]
    assert [a["name"] for a in upload["vars"]["pbs_client_archives"]] == ["data.pxar", "cache.pxar"]

    remove = list(tasks_recursive(load_playbook(ROOT / "ansible/playbooks/apps/remove.yml")))
    delete = task_named(remove, "Delete the data and cache paths")
    assert "{{ app_config.app.cache_path | default('') }}" in delete["loop"]



def check_restore():
    swap = swap_inputs("ntfy", "restore-native.yml")
    assert swap["recovery_swap_runtime"] == "native"
    assert swap["recovery_swap_archives"] == ["data.pxar", "cache.pxar"]
    data, cache = swap["recovery_swap_paths"]
    assert (data["path"], data["source"]) == ("{{ app_config.app.data_path }}", "data")
    assert (cache["path"], cache["source"]) == ("{{ app_config.app.cache_path }}", "cache")
    for path in (data, cache):
        assert (path["owner"], path["group"], path["mode"]) == ("ntfy", "ntfy", "0750")
    # An empty message cache is a valid point; a point without the auth database is not.
    assert cache["allow_empty"] is True and not data.get("allow_empty", False)
    staged = (ROOT / "ansible/roles/ntfy/tasks/restore-staged.yml").read_text()
    assert "{{ recovery_swap_stage }}/data/{{ _ntfy_restore_auth_relative }}" in staged
    restore_native = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/restore-native.yml").read_text())
    reconcile = next(task for task in restore_native
                     if task.get("ansible.builtin.include_tasks") == "credentials-native.yml")
    swap_task = next(task for task in restore_native
                     if task.get("ansible.builtin.include_tasks", "").endswith("tasks/recovery/restore-swap.yml"))
    assert restore_native.index(reconcile) > restore_native.index(swap_task)
    assert reconcile["when"] == "restore_overwrite | bool"
    native_deploy = (ROOT / "ansible/roles/ntfy/tasks/main.yml").read_text()
    assert "ansible.builtin.include_tasks: credentials-native.yml" in native_deploy
    native_credentials = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/credentials-native.yml").read_text())
    assert any(task.get("ansible.builtin.import_tasks", "").endswith(
        "tasks/bootstrap/configure-unattended-upgrades.yml") for task in native_credentials)
    verified = (ROOT / "ansible/roles/ntfy/tasks/restore-verified.yml").read_text()
    assert "argv: [ntfy, user, list]" in verified
    assert swap["recovery_swap_health_url"].endswith("/v1/health")


def main():
    check_contract()
    check_restore()
    print("Ntfy: dual-archive PBS recovery contract and restore swap inputs passed")


if __name__ == "__main__":
    main()
