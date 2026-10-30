#!/usr/bin/env python3
"""Run Immich's restore-swap hooks and check its swap inputs.

gate/test-restore-swap.py proves the swap, the mount handling and rollback of the media.
This test proves what Immich adds: the staged point must hold a database dump kept private
from the media owner, the live database is dumped once Immich stops, the archived dump
replaces it, and rollback loads the pre-restore dump only after the archived one was loaded.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/immich"


def hook_tasks(hook, log):
    """The shared database tasks are the boundary; record each dump and restore file."""
    tasks = yaml.safe_load((role / f"tasks/restore-{hook}.yml").read_text())
    for task in tasks:
        include = str(task.get("ansible.builtin.include_tasks", ""))
        for shared, key in [("database/dump.yml", "database_dump_file"),
                            ("database/restore.yml", "database_restore_file")]:
            if include.endswith(shared):
                assert task["vars"]["database_app_config"] == "{{ app_config.app.database }}"
                del task["ansible.builtin.include_tasks"]
                task["ansible.builtin.shell"] = (
                    f"echo {shared.split('/')[1].removesuffix('.yml')} "
                    f"$(basename {task['vars'][key]}) >> {log}")
                task.pop("vars")
    return tasks


def run(hooks, stage, fail_after=None):
    log = stage.parent / "database.log"
    log.unlink(missing_ok=True)
    tasks = [task for hook in hooks for task in hook_tasks(hook, log)]
    if fail_after:
        position = next(i for i, task in enumerate(tasks) if task.get("name") == fail_after) + 1
        tasks.insert(position, {"ansible.builtin.fail": {"msg": "fixture failure"}})
    rollback = hook_tasks("rollback", log)
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "immich", "recovery_swap_stage": str(stage),
                     "app_config": {"app": {"database": {"name": "immich"}}}},
            "tasks": [{"block": tasks, "rescue": rollback + [{"ansible.builtin.fail": {"msg": "rolled back"}}]}],
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                                env=env, text=True, capture_output=True, check=False)
    return result, (log.read_text().splitlines() if log.exists() else [])


def main():
    swap = swap_inputs("immich")
    assert swap["recovery_swap_archives"] == ["database.pxar", "media.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _immich_restore_media_path }}", "source": "media",
        "owner": "{{ app_config.media_storage.owner.uid | int }}",
        "group": "{{ app_config.media_storage.owner.gid | int }}",
        "mount": True}]
    assert swap["recovery_swap_stop_services"] == ["immich-server", "immich-machine-learning"]
    assert swap["recovery_swap_compose_project"] == "{{ immich_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/api/server/ping")
    for hook in ["staged", "stopped", "replaced", "rollback"]:
        assert swap[f"recovery_swap_{hook}_hook"] == f"{{{{ role_path }}}}/tasks/restore-{hook}.yml"

    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory) / "stage"
        (stage / "database").mkdir(parents=True, mode=0o711)
        result, _ = run(["staged"], stage)
        assert result.returncode != 0, "a point without a dump must be refused"
        (stage / "database/immich.dump").write_bytes(b"")
        result, _ = run(["staged"], stage)
        assert result.returncode != 0, "an empty dump must be refused"
        (stage / "database/immich.dump").write_bytes(b"PGDMP")
        result, _ = run(["staged"], stage)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (stage / "database").stat().st_mode & 0o777 == 0o700

        result, calls = run(["stopped", "replaced"], stage)
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == ["dump rollback.dump", "restore immich.dump"], calls
        # A failure after the stop but before the archived dump loads leaves the database alone.
        result, calls = run(["stopped", "replaced"], stage,
                            fail_after="Immich restore | Dump the live database for rollback")
        assert result.returncode != 0 and calls == ["dump rollback.dump"], calls
        # A failure once the archived dump started loading puts the pre-restore dump back.
        result, calls = run(["stopped", "replaced"], stage,
                            fail_after="Immich restore | Restore the named PostgreSQL database")
        assert result.returncode != 0
        assert calls == ["dump rollback.dump", "restore immich.dump", "restore rollback.dump"], calls
    print("Immich: restore swap inputs, private staged dump and database rollback hooks passed")


if __name__ == "__main__":
    main()
