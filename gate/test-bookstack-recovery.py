#!/usr/bin/env python3
"""Check BookStack's restore wiring and exercise its backup helper with command shims.

gate/test-restore-swap.py proves the swap and its database rollback;
gate/test-pre-restore-point-age.py proves the one-hour pre-restore point rule.
"""

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "ansible/roles/bookstack/files/bookstack-recovery"
REMOVE = yaml.safe_load((ROOT / "ansible/playbooks/apps/remove.yml").read_text())
BOOKSTACK_DEFAULTS = yaml.safe_load((ROOT / "ansible/vars/app-defaults/bookstack.yml").read_text())["bookstack_defaults"]
BOOKSTACK_ROLE = yaml.safe_load((ROOT / "ansible/roles/bookstack/tasks/main.yml").read_text())
BOOKSTACK_RESTORE = yaml.safe_load((ROOT / "ansible/playbooks/maintenance/bookstack-restore.yml").read_text())


# Plays the shared database client: `docker run ... -v DIR:/work[:ro] ... --entrypoint /bin/sh
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
    -v)
      case "$2" in
        *:/work) work=${2%:/work} ;;
        *:/work:ro) work=${2%:/work:ro} ;;
      esac
      shift 2
      ;;
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
[[ "$action" == dump ]] || exit 94
printf 'DATABASE\\n' > "$file"
"""

# Plays the shared PBS client script: `backup --id ID --keep-last N --keep-daily D NAME:PATH...`.
# gate/test-pbs-client.py proves the script itself.
PBS = """#!/bin/bash
set -eu
[[ "$1" == backup && "$2" == --id && "$3" == bookstack ]] || exit 93
printf '%s\\n' "${@:8}" >> "$BOOKSTACK_TEST_CLIENT_LOG"
[[ "$BOOKSTACK_TEST_CASE" != upload-failure ]] || exit 8
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


def check_remove_contract():
    tasks = list(tasks_recursive(REMOVE))
    database = BOOKSTACK_DEFAULTS["app"]["database"]
    assert database["provider"] == "mariadb"
    assert database["instance"]
    assert database["name"] and database["role"]

    decision = next(task for task in tasks
                    if task.get("name") == "Decide whether Remove drops a named database")
    expression = str(decision["ansible.builtin.set_fact"]["_rm_drops_database"])
    assert "delete_data" in expression
    assert "app_config.app.database.provider" in expression
    assert "app_config.app.database.instance" in expression

    resolve = next(task for task in tasks
                   if task.get("name") == "Resolve the app's named database before unwiring")
    assert resolve["ansible.builtin.include_tasks"].endswith("database/resolve-app-database.yml")
    guard = next(task for task in tasks
                 if task.get("name") == "Check the app's named database ownership before unwiring")
    assert guard["ansible.builtin.include_tasks"].endswith("database/guard-app-database.yml")

    drop = next(task for task in tasks
                if task.get("name") == "Drop the app's named database and login")
    assert drop["ansible.builtin.include_tasks"].endswith("database/drop.yml")
    assert "_rm_drops_database" in str(drop["when"])
    drop_play = next(play for play in REMOVE
                     if play.get("name") == "Remove App | Drop its named database")
    assert drop_play["hosts"] == "localhost"
    assert REMOVE.index(drop_play) > next(
        index for index, play in enumerate(REMOVE)
        if play.get("name", "").startswith("Remove App | Stop ")
    )
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


def check_restore_contract():
    swap = swap_inputs("bookstack")
    assert swap["recovery_swap_archives"] == ["database.pxar", "storage.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _bookstack_restore_data_path }}", "source": "storage",
        "owner": "{{ app_config.app.puid }}", "group": "{{ app_config.app.pgid }}", "mode": "0750"}]
    deployed = next(task for task in BOOKSTACK_ROLE
                    if task.get("name") == "BookStack | Create project and durable data directories")
    assert {"path": "{{ app_config.app.data_path }}", "owner": "{{ app_config.app.puid }}",
            "group": "{{ app_config.app.pgid }}", "mode": "0750"} in deployed["loop"], \
        "the restore gives /config the owner Deploy gives it"
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/database.sql"}
    assert swap["recovery_swap_backup_id"] == swap["recovery_swap_pre_restore_backup_id"] \
        == "{{ _bookstack_restore_backup_id }}"
    assert swap["recovery_swap_stop_services"] == ["bookstack"]
    assert swap["recovery_swap_health_url"].endswith("/login")
    restore = yaml.safe_load((ROOT / "ansible/roles/bookstack/tasks/restore.yml").read_text())
    age = next(task for task in restore if "require-recent-pre-restore-point" in
               str(task.get("ansible.builtin.include_tasks", "")))
    assert age["when"] == "restore_overwrite | bool"
    assert restore.index(age) < restore.index(next(task for task in restore if "restore-swap" in
                                                   str(task.get("ansible.builtin.include_tasks", ""))))

    validate = next(task for task in BOOKSTACK_RESTORE[0]["pre_tasks"]
                    if task.get("ansible.builtin.assert"))
    requirements = validate["ansible.builtin.assert"]["that"]
    assert any("pre_restore_point" in item and "length > 0" in item for item in requirements)
    assert any("pre_restore_point != snapshot" in item for item in requirements)

    tasks = list(tasks_recursive(BOOKSTACK_RESTORE))
    run = next(task for task in tasks if task.get("name") == "Plan or perform the BookStack restore")
    assert run["ansible.builtin.include_role"] == {"name": "bookstack", "tasks_from": "restore"}
    for name in ("app_config", "homelabinfra_infra", "homelabinfra_vault"):
        assert run["vars"][name] == "{{ hostvars['localhost'].%s }}" % name, name
    assert run["vars"]["restore_pre_restore_point"] == "{{ pre_restore_point | default('') }}"
    check = next(task for task in tasks if task.get("name") == "Fail if BookStack restore did not complete")
    assert "bookstack_restore_complete" in str(check["ansible.builtin.assert"]["that"])
    assert "homelab-infra-bookstack-recovery" not in str(BOOKSTACK_RESTORE), "the guest helper only backs up"


def check_backup(case):
    with tempfile.TemporaryDirectory(prefix="bookstack-recovery-test-") as temporary:
        root = Path(temporary)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_executable(bin_dir / "docker", DOCKER)
        write_executable(bin_dir / "pbs-client.sh", PBS)
        project = root / "project"
        project.mkdir()
        data = root / "app-data"
        data.mkdir()
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
            "PBS_CLIENT_SCRIPT": str(bin_dir / "pbs-client.sh"),
            "PBS_CLIENT_CONFIG": str(root / "pbs-client.env"),
            "PBS_BACKUP_ID": "bookstack",
            "PBS_RETENTION_DAYS": "14",
        }
        config.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
        environment = os.environ | {
            "BOOKSTACK_RECOVERY_CONFIG": str(config),
            "BOOKSTACK_TEST_CASE": case,
            "BOOKSTACK_TEST_COMPOSE_LOG": str(compose_log),
            "BOOKSTACK_TEST_CLIENT_LOG": str(client_log),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }
        result = subprocess.run([str(RECOVERY), "--backup"], env=environment, text=True,
                                capture_output=True, check=False)
        assert (result.returncode == 0) == (case == "success"), result.stdout + result.stderr
        assert compose_log.read_text().splitlines() == ["stop", "start"], "BookStack runs again"
        lines = client_log.read_text().splitlines()
        assert lines[0] == "dump" and lines[1].startswith("database.pxar:/var/tmp/homelab-bookstack.")
        assert lines[2] == f"storage.pxar:{data}"
        assert not Path(lines[1].split(":", 1)[1]).parent.exists(), "the work directory is removed"
        for mode in ("--restore", "--plan"):
            assert subprocess.run([str(RECOVERY), mode], env=environment, capture_output=True).returncode == 2


def main():
    check_remove_contract()
    check_admin_email_contract()
    check_backup_scheduler_contract()
    check_restore_contract()
    check_backup("success")
    check_backup("upload-failure")
    print("BookStack: restore through the shared swap, backup helper and its restart passed")


if __name__ == "__main__":
    main()
