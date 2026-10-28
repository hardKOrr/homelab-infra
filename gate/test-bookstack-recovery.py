#!/usr/bin/env python3
"""Exercise BookStack restore and its rollback with isolated command shims."""

import os
import re
import shlex
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "ansible/roles/bookstack/files/bookstack-recovery"
SNAPSHOT = "host/bookstack/2026-10-15T00:00:00Z"
BOOKSTACK_ROLE = yaml.safe_load((ROOT / "ansible/roles/bookstack/tasks/main.yml").read_text())
BOOKSTACK_RESTORE = yaml.safe_load((ROOT / "ansible/playbooks/maintenance/bookstack-restore.yml").read_text())


# Plays the shared database client: `docker run ... -v DIR:/work ... --entrypoint /bin/sh
# IMAGE /usr/local/bin/database-client.sh ACTION /work/FILE`.
DOCKER = """#!/bin/bash
set -eu
if [[ "$1" == "compose" ]]; then
  action=
  for arg in "$@"; do
    if [[ "$arg" == "stop" || "$arg" == "start" ]]; then action="$arg"; fi
  done
  printf '%s\\n' "$action" >> "$BOOKSTACK_TEST_COMPOSE_LOG"
  exit 0
fi
[[ "$1" == "run" ]] || exit 90
shift
work= entrypoint= image= args=()
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -v) [[ "$2" == *:/work ]] && work=${2%:/work}; shift 2 ;;
    -e) shift 2 ;;
    --entrypoint) entrypoint="$2"; shift 2 ;;
    --rm | --network) [[ "$1" == --network ]] && shift; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
[[ "$entrypoint" == /bin/sh && "${args[0]}" == mysql-client:test ]] || exit 91
[[ "${args[1]}" == /usr/local/bin/database-client.sh ]] || exit 92
[[ "$DB_ENGINE" == mariadb && "$DB_NAME" == bookstack && "$DB_USER" == bookstack ]] || exit 93
action=${args[2]} file="$work/${args[3]#/work/}"
printf '%s\\n' "$action" >> "$BOOKSTACK_TEST_CLIENT_LOG"
case "$action" in
  dump) cp "$BOOKSTACK_TEST_DB_STATE" "$file" ;;
  restore)
    if [[ "$BOOKSTACK_TEST_CASE" == "import-failure" ]] && grep -q SNAPSHOT-DATABASE "$file"; then
      printf 'PARTIAL-DATABASE\\n' > "$BOOKSTACK_TEST_DB_STATE"
      exit 17
    fi
    cp "$file" "$BOOKSTACK_TEST_DB_STATE" ;;
  *) exit 94 ;;
esac
"""

PBS = """#!/bin/bash
set -eu
if [[ "$1" == "snapshot" && "$2" == "files" ]]; then
  [[ "$3" == "$BOOKSTACK_TEST_PRE_RESTORE_POINT" ]] || exit 94
  [[ "${BOOKSTACK_TEST_MISSING_PREPOINT:-0}" != 1 ]] || exit 95
  exit 0
fi
if [[ "$1" == "restore" ]]; then
  destination="$4"
  mkdir -p "$destination"
  case "$3" in
    database.pxar) cp -a "$BOOKSTACK_TEST_SNAPSHOT_DB/." "$destination/" ;;
    storage.pxar) cp -a "$BOOKSTACK_TEST_SNAPSHOT_STORAGE/." "$destination/" ;;
    *) exit 92 ;;
  esac
  exit 0
fi
exit 93
"""


def write_executable(path, content):
    path.write_text(content)
    path.chmod(0o700)


def tasks_recursive(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks_recursive(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks_recursive(value.get(key, []))


def check_admin_email_contract():
    task = next(task for task in BOOKSTACK_ROLE
                if task.get("name") == "BookStack | Validate administrator email")
    expression = task["ansible.builtin.assert"]["that"][0]
    match = re.search(r"match\('([^']+)'\)", expression)
    assert match
    pattern = match.group(1).replace("\\\\", "\\")
    assert re.fullmatch(pattern, "admin@example.invalid"), "the documented default must pass"
    for invalid in ["admin", "admin@invalid", "admin user@example.invalid"]:
        assert not re.fullmatch(pattern, invalid), invalid


def check_backup_scheduler_contract():
    names = [task.get("name") for task in BOOKSTACK_ROLE]
    install = next(task for task in BOOKSTACK_ROLE
                   if task.get("name") == "BookStack | Install cron for recovery scheduling")
    service = next(task for task in BOOKSTACK_ROLE
                   if task.get("name") == "BookStack | Ensure cron is running for recovery scheduling")
    schedule = next(task for task in BOOKSTACK_ROLE
                    if task.get("name") == "BookStack | Schedule application-consistent backup")
    assert install["ansible.builtin.apt"] == {"name": "cron", "state": "present"}
    assert service["ansible.builtin.service"] == {"name": "cron", "state": "started", "enabled": True}
    backup_condition = "bookstack_pbs_available | default(false) | bool and app_config.backup.enabled | default(false) | bool"
    assert install["when"] == backup_condition
    assert service["when"] == backup_condition
    assert schedule["when"] == backup_condition
    assert names.index(install["name"]) < names.index(schedule["name"])
    assert names.index(service["name"]) < names.index(schedule["name"])


def check_pre_restore_point_contract():
    run = next(task for task in tasks_recursive(BOOKSTACK_RESTORE)
               if task.get("name") == "Run the destructive BookStack restore")
    argv = run["ansible.builtin.command"]["argv"]
    assert "--pre-restore-point" in argv
    assert "{{ pre_restore_point }}" in argv
    validate = next(task for task in BOOKSTACK_RESTORE[0]["pre_tasks"]
                    if task.get("ansible.builtin.assert"))
    requirements = validate["ansible.builtin.assert"]["that"]
    assert any("pre_restore_point" in item and "length > 0" in item for item in requirements)
    assert any("pre_restore_point != snapshot" in item for item in requirements)
    group_check = next(task for task in tasks_recursive(BOOKSTACK_RESTORE)
                       if task.get("name") == "Validate the BookStack safety snapshot belongs to this recovery group")
    assert "app_config.backup.backup_id" in str(group_check["ansible.builtin.assert"]["that"])


def check_restore_result_contract():
    tasks = list(tasks_recursive(BOOKSTACK_RESTORE))
    plan = next(task for task in tasks if task.get("name") == "Run the BookStack restore plan")
    restore = next(task for task in tasks if task.get("name") == "Run the destructive BookStack restore")
    check = next(task for task in tasks if task.get("name") == "Fail if BookStack restore did not complete")
    assert plan["register"] == "bookstack_restore_plan_result"
    assert restore["register"] == "bookstack_restore_result"
    assertion = str(check["ansible.builtin.assert"]["that"])
    assert "bookstack_restore_plan_result.rc" in assertion
    assert "bookstack_restore_result.rc" in assertion


def check(case):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    pre_restore_time = now
    if case == "stale-prepoint":
        pre_restore_time -= timedelta(hours=2)
    elif case == "future-prepoint":
        pre_restore_time += timedelta(minutes=5)
    pre_restore_point = f"host/bookstack/{pre_restore_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    if case == "invalid-time-prepoint":
        pre_restore_point = "host/bookstack/not-a-timestamp"

    with tempfile.TemporaryDirectory(prefix="bookstack-recovery-test-") as temporary:
        root = Path(temporary)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_executable(bin_dir / "docker", DOCKER)
        write_executable(bin_dir / "proxmox-backup-client", PBS)

        project = root / "project"
        project.mkdir()
        data = root / "app-data"
        data.mkdir()
        data.chmod(0o750)
        (data / "original.txt").write_text("original attachment state\n")
        original_inode = data.stat().st_ino
        database = root / "database-state.sql"
        database.write_text("ORIGINAL-DATABASE\n")

        snapshot_database = root / "snapshot-database"
        snapshot_database.mkdir()
        (snapshot_database / "database.sql").write_text("SNAPSHOT-DATABASE\n")
        snapshot_storage = root / "snapshot-storage"
        snapshot_storage.mkdir()
        (snapshot_storage / "restored.txt").write_text("restored attachment state\n")

        compose_log = root / "compose.log"
        client_log = root / "client.log"
        config = root / "bookstack.env"
        values = {
            "BOOKSTACK_PROJECT_DIR": str(project),
            "BOOKSTACK_SERVICE": "bookstack",
            "BOOKSTACK_DATA_PATH": str(data),
            "BOOKSTACK_DB_HOST": "127.0.0.1",
            "BOOKSTACK_DB_PROVIDER": "mariadb",
            "BOOKSTACK_DB_PORT": "3306",
            "BOOKSTACK_DB_NAME": "bookstack",
            "BOOKSTACK_DB_USER": "bookstack",
            "BOOKSTACK_DB_PASSWORD": "test-only",
            "BOOKSTACK_DB_CLIENT_IMAGE": "mysql-client:test",
            "DATABASE_CLIENT_SCRIPT": "/usr/local/lib/homelab-infra/database-client.sh",
            "PBS_REPOSITORY": "test:store",
            "PBS_FINGERPRINT": "test",
            "PBS_PASSWORD": "test-only",
            "PBS_BACKUP_ID": "bookstack",
            "PBS_RETENTION_DAYS": "14",
        }
        config.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
        environment = os.environ | {
            "BOOKSTACK_RECOVERY_CONFIG": str(config),
            "BOOKSTACK_TEST_CASE": case,
            "BOOKSTACK_TEST_COMPOSE_LOG": str(compose_log),
            "BOOKSTACK_TEST_CLIENT_LOG": str(client_log),
            "BOOKSTACK_TEST_DB_STATE": str(database),
            "BOOKSTACK_TEST_SNAPSHOT_DB": str(snapshot_database),
            "BOOKSTACK_TEST_SNAPSHOT_STORAGE": str(snapshot_storage),
            "BOOKSTACK_TEST_PRE_RESTORE_POINT": pre_restore_point,
            "BOOKSTACK_TEST_MISSING_PREPOINT": "1" if case == "missing-prepoint" else "0",
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }
        result = subprocess.run(
            [str(RECOVERY), "--restore", "--snapshot", SNAPSHOT,
             "--pre-restore-point", pre_restore_point],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        compose_actions = compose_log.read_text().splitlines() if compose_log.exists() else []
        client_commands = client_log.read_text().splitlines() if client_log.exists() else []
        if case == "success":
            assert result.returncode == 0, result.stdout + result.stderr
            assert (data / "restored.txt").read_text() == "restored attachment state\n"
            assert not (data / "original.txt").exists()
            assert data.stat().st_ino != original_inode, "successful restore must replace the old tree"
            assert data.stat().st_mode & 0o777 == 0o750
            assert database.read_text() == "SNAPSHOT-DATABASE\n"
            assert compose_actions == ["stop", "start"], compose_actions
            assert client_commands == ["dump", "restore"], client_commands
        elif case == "import-failure":
            assert result.returncode != 0, result.stdout + result.stderr
            assert "BookStack rollback restored the previous data and database." in result.stderr
            assert (data / "original.txt").read_text() == "original attachment state\n"
            assert not (data / "restored.txt").exists()
            assert data.stat().st_ino == original_inode, "rollback must rename the original tree back"
            assert data.stat().st_mode & 0o777 == 0o750
            assert database.read_text() == "ORIGINAL-DATABASE\n"
            assert compose_actions == ["stop", "stop", "start"], compose_actions
            assert client_commands == ["dump", "restore", "restore"], client_commands
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert "restore failed" not in result.stderr.lower()
            if case == "stale-prepoint":
                assert "must be no more than one hour old" in result.stderr
            elif case == "future-prepoint":
                assert "timestamp is in the future" in result.stderr
            elif case == "invalid-time-prepoint":
                assert "must use a valid PBS UTC timestamp" in result.stderr
            assert (data / "original.txt").read_text() == "original attachment state\n"
            assert data.stat().st_ino == original_inode
            assert database.read_text() == "ORIGINAL-DATABASE\n"
            assert compose_actions == [], compose_actions
            assert client_commands == [], client_commands
        assert not list(root.glob("app-data.pre-restore.*")), "completed restore must remove its rollback tree"


def main():
    check_admin_email_contract()
    check_backup_scheduler_contract()
    check_pre_restore_point_contract()
    check_restore_result_contract()
    check("success")
    check("import-failure")
    check("missing-prepoint")
    check("stale-prepoint")
    check("future-prepoint")
    check("invalid-time-prepoint")
    print("BookStack: recent restore points, plan separation, and restore rollback passed")


if __name__ == "__main__":
    main()
