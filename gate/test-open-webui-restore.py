#!/usr/bin/env python3
"""Check Open WebUI's restore-swap inputs, its lock and its key hooks.

gate/test-restore-swap.py proves the swap. Here: the restore holds the lock the guest's
backup timer takes for the whole swap, the staged hook checks webui.db with the pinned
image, the replaced hook gives the target the source's application key, and the rollback
hook gives it its own key back.
"""

import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import repo, run_hook, swap_inputs

tasks = yaml.safe_load((repo / "ansible/roles/open-webui/tasks/restore.yml").read_text())
staged = yaml.safe_load((repo / "ansible/roles/open-webui/tasks/restore-staged.yml").read_text())


def task_named(name):
    return next(task for task in tasks if task.get("name") == name)


def check_swap_and_lock():
    swap = swap_inputs("open-webui")
    assert swap["recovery_swap_runtime"] == "docker"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ app_config.app.data_path }}", "owner": "root", "group": "root", "mode": "0750"}]
    assert swap["recovery_swap_backup_id"] == "{{ _open_webui_restore_backup_id }}"
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _open_webui_target_backup_id }}"
    assert swap["recovery_swap_stop_services"] == ["{{ app_config.app.service_name }}"]
    assert swap["recovery_swap_health_url"].endswith("/health")

    lock = "/run/lock/homelab-infra-open-webui-{{ _open_webui_restore_target }}.lock"
    acquire = task_named("Open WebUI restore | Acquire the shared backup/restore lock")
    assert acquire["ansible.builtin.command"]["argv"][-1] == lock
    assert acquire["when"] == "restore_overwrite | bool"
    held = task_named("Open WebUI restore | Preview or replace the data directory under the lock")
    assert held["block"][0]["ansible.builtin.include_tasks"].endswith("tasks/recovery/restore-swap.yml")
    release = held["always"][0]
    assert release["ansible.builtin.file"] == {"path": lock, "state": "absent"}
    assert release["when"] == "_open_webui_restore_lock is changed", "release only a lock it took"
    guest = (repo / "ansible/roles/open-webui/files/open-webui-recovery").read_text()
    assert 'lock="/run/lock/homelab-infra-open-webui-${INSTANCE}.lock"' in guest

    keys = task_named("Open WebUI restore | Require the source and target application keys")
    assert keys["when"] == "restore_overwrite | bool"

    argv = staged[0]["ansible.builtin.command"]["argv"]
    assert "{{ recovery_swap_stage }}/data:/source:ro" in argv and "none" in argv
    assert argv[argv.index("--entrypoint") + 2] == "{{ app_config.app.image }}"
    assert "PRAGMA integrity_check" in argv[-1]


def stub_vault(log):
    def stub(hook_tasks):
        for task in hook_tasks:
            for inner in [task, *task.get("block", [])]:
                inner.pop("no_log", None)
                if str(inner.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
                    inner.pop("ansible.builtin.include_tasks")
                    inner["ansible.builtin.shell"] = (
                        "echo '{{ vault_item_name }} {{ vault_item_fields.secret_key }}' >> " + str(log))
        return hook_tasks
    return stub


def check_key_hooks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        env, log = root / "open-webui.env", root / "vault.log"
        env.write_text("WEBUI_SECRET_KEY=target-key\nENABLE_SIGNUP=false\n")
        variables = {
            "_open_webui_restore_target": "open-webui-restore",
            "open_webui_env_file": str(env),
            "restore_source_open_webui_secret_key": "source-key",
            "_open_webui_restore_target_secret_key": "target-key",
        }

        # Without a replaced key, the rollback hook changes nothing.
        result = run_hook("open-webui", "rollback", variables, stub_vault(log))
        assert result.returncode == 0, result.stdout + result.stderr
        assert not log.exists() and "target-key" in env.read_text()

        replaced = run_hook("open-webui", "replaced", variables, stub_vault(log))
        assert replaced.returncode == 0, replaced.stdout + replaced.stderr
        assert env.read_text() == "WEBUI_SECRET_KEY=source-key\nENABLE_SIGNUP=false\n"
        assert log.read_text() == "homelab-infra/apps/open-webui-restore source-key\n"

        result = run_hook("open-webui", "rollback",
                          variables | {"_open_webui_restore_key_replaced": True}, stub_vault(log))
        assert result.returncode == 0, result.stdout + result.stderr
        assert env.read_text() == "WEBUI_SECRET_KEY=target-key\nENABLE_SIGNUP=false\n"
        assert log.read_text().splitlines()[-1] == "homelab-infra/apps/open-webui-restore target-key"


if __name__ == "__main__":
    check_swap_and_lock()
    check_key_hooks()
    print("ok Open WebUI restore: swap inputs, lock, webui.db check and key hooks")
