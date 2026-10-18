#!/usr/bin/env python3
"""Guard Uptime Kuma's native PBS backup and in-place recovery contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

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
    assert "one-shot helper container" in (
        ROOT / "ansible/vars/app-defaults/uptime-kuma.yml"
    ).read_text()

    backup_tasks = list(tasks_recursive(BACKUP))
    stop = task_named(backup_tasks, "Uptime Kuma backup | Stop only the Uptime Kuma service")
    assert stop["community.docker.docker_compose_v2"]["state"] == "stopped"
    assert stop["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]
    restart = task_named(backup_tasks, "Uptime Kuma backup | Start only the Uptime Kuma service")
    assert restart["community.docker.docker_compose_v2"]["state"] == "present"
    assert restart["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]

    archive_block = task_named(
        backup_tasks, "Uptime Kuma backup | Archive and verify the quiesced data directory"
    )
    upload = task_named(archive_block["block"], "Uptime Kuma backup | Push and verify a new PBS point")
    script = upload["ansible.builtin.command"]["argv"][-1]
    snapshot_list_positions = [
        index for index in range(len(script))
        if script.startswith('snapshot list "host/$BACKUP_ID"', index)
    ]
    assert len(snapshot_list_positions) == 2
    assert snapshot_list_positions[0] < script.index(
        "proxmox-backup-client backup data.pxar:/source"
    ) < snapshot_list_positions[1]
    assert "len(after) <= len(before)" in script
    assert "before_times" in script and "added" in script
    assert "proxmox-backup-client snapshot files \"$snapshot\"" in script
    assert "grep -q 'data.pxar'" in script
    assert script.index("grep -q 'data.pxar'") < script.index("proxmox-backup-client prune")
    assert upload.get("no_log") is True
    assert set(upload["environment"]) == {
        "PBS_REPOSITORY", "PBS_FINGERPRINT", "PBS_PASSWORD", "BACKUP_ID"
    }
    for task in archive_block["always"]:
        if task.get("community.docker.docker_compose_v2"):
            assert task["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]


def check_backup_snapshot_delta():
    upload = task_named(
        list(tasks_recursive(BACKUP)), "Uptime Kuma backup | Push and verify a new PBS point"
    )
    script = upload["ansible.builtin.command"]["argv"][-1]
    marker = 'snapshot=$(python3 - "$BACKUP_ID" <<\'PY\'\n'
    body = script.split(marker, 1)[1].split("\nPY", 1)[0]
    cases = [
        ("no-new-snapshot", [{"backup-time": 100}], [{"backup-time": 100}], False),
        ("new-distinct-snapshot", [{"backup-time": 100}], [{"backup-time": 100}, {"backup-time": 200}], True),
    ]
    for name, before, after, expected_success in cases:
        with tempfile.TemporaryDirectory(prefix="homelab-uptime-kuma-pbs-") as directory:
            root = Path(directory)
            (root / "before.json").write_text(json.dumps(before), encoding="utf-8")
            (root / "after.json").write_text(json.dumps(after), encoding="utf-8")
            test_body = body.replace("/tmp/snapshots-before.json", str(root / "before.json"))
            test_body = test_body.replace("/tmp/snapshots-after.json", str(root / "after.json"))
            result = subprocess.run(
                [sys.executable, "-", "uptime-kuma"], input=test_body, text=True,
                capture_output=True, check=False,
            )
            assert (result.returncode == 0) == expected_success, (name, result.stdout, result.stderr)
            if expected_success:
                assert result.stdout.startswith("host/uptime-kuma/")


def check_restore_contract_and_rollback():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/uptime-kuma.yml").read_text()
    )["uptime_kuma_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]

    restore_tasks = list(tasks_recursive(RESTORE))
    source_guard = task_named(
        restore_tasks, "Uptime Kuma restore | Require the selected point to belong to its backup group"
    )
    assert "_kuma_restore_source_backup_id" in str(source_guard["ansible.builtin.assert"]["that"])
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
        block, "Uptime Kuma restore | Validate both PBS archives and extract the selected point"
    )
    assert validate.get("no_log") is True
    command_argv = validate["ansible.builtin.command"]["argv"]
    assert command_argv[-1].count("snapshot files") >= 1
    assert command_argv[-1].count("grep -q 'data.pxar'") >= 2
    assert command_argv[command_argv.index("RESTORE_DESTINATION") - 1] == "--env"
    assert "RESTORE_DESTINATION" in validate["environment"]
    assert "PRE_RESTORE_POINT" in validate["environment"]
    compose_tasks = [task for task in restore_tasks if "community.docker.docker_compose_v2" in task]
    assert compose_tasks
    for task in compose_tasks:
        assert task["community.docker.docker_compose_v2"]["services"] == ["uptime-kuma"]

    for before, after in [
        ("Uptime Kuma restore | Validate both PBS archives and extract the selected point",
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


def check_pre_restore_point_gate():
    validate = task_named(
        list(tasks_recursive(RESTORE)),
        "Uptime Kuma restore | Validate both PBS archives and extract the selected point",
    )
    script = validate["ansible.builtin.command"]["argv"][-1]
    start = script.index('source_files=$(proxmox-backup-client snapshot files "$SNAPSHOT"')
    end = script.index("proxmox-backup-client restore", start)
    verification = script[start:end]
    assert 'if [ "$RESTORE_DESTINATION" = "existing" ]; then' in verification

    with tempfile.TemporaryDirectory(prefix="homelab-uptime-kuma-restore-gate-") as directory:
        root = Path(directory)
        bindir = root / "bin"
        bindir.mkdir()
        call_log = root / "pbs-calls.jsonl"
        fake_pbs = bindir / "proxmox-backup-client"
        fake_pbs.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['PBS_CALL_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "print(json.dumps(['data.pxar']))\n",
            encoding="utf-8",
        )
        fake_pbs.chmod(0o700)

        for destination, prepoint, expected_calls in [
            ("existing", "host/uptime-kuma/2026-10-18T00:00:00Z", 2),
            ("new", "", 1),
        ]:
            call_log.unlink(missing_ok=True)
            env = os.environ | {
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "PBS_CALL_LOG": str(call_log),
                "SNAPSHOT": "host/uptime-kuma/2026-10-17T00:00:00Z",
                "PRE_RESTORE_POINT": prepoint,
                "RESTORE_DESTINATION": destination,
            }
            result = subprocess.run(
                ["/bin/sh", "-ceu", verification], env=env,
                text=True, capture_output=True, check=False,
            )
            assert result.returncode == 0, (destination, result.stdout, result.stderr)
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            assert len(calls) == expected_calls, (destination, calls)
            assert calls[0][2] == "host/uptime-kuma/2026-10-17T00:00:00Z"
            if destination == "existing":
                assert calls[1][2] == prepoint


def main():
    check_contract_and_backup()
    check_backup_snapshot_delta()
    check_restore_contract_and_rollback()
    check_pre_restore_point_gate()
    print("Uptime Kuma: native PBS archive verification, destination guards and rollback order passed")


if __name__ == "__main__":
    main()
