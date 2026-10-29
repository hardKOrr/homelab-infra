#!/usr/bin/env python3
"""Check MySQL's declared PBS contract and the shared server recovery script's reload restore."""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import stat
import tempfile
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/mysql"
HELPER_PATH = ROOT / "ansible/tasks/database/scripts/server-recovery.py"
SPEC = spec_from_file_location("server_recovery", HELPER_PATH)
MYSQL_RECOVERY = module_from_spec(SPEC)
SPEC.loader.exec_module(MYSQL_RECOVERY)


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


def task_tree(path):
    return list(tasks_recursive(yaml.safe_load(path.read_text(encoding="utf-8"))))


def install_mysql_stubs(directory):
    directory.mkdir()
    capture = directory / "capture"
    capture.mkdir()
    mysql = directory / "mysql"
    mysql.write_text(
        """#!/bin/sh
case "$*" in
  *"SELECT VERSION()"*) printf '%s\\n' "${MYSQL_FIXTURE_VERSION:-8.4.5}" ;;
  *INFORMATION_SCHEMA.SCHEMATA*) printf '%s\\n' 'DROP DATABASE IF EXISTS `fixture`;';;
  *"SELECT 1"*) printf '1\\n' ;;
  *--execute*) : ;;
  *)
    count=0
    [ ! -f "$MYSQL_FIXTURE_CAPTURE/count" ] || count=$(cat "$MYSQL_FIXTURE_CAPTURE/count")
    count=$((count + 1))
    printf '%s\\n' "$count" > "$MYSQL_FIXTURE_CAPTURE/count"
    cat > "$MYSQL_FIXTURE_CAPTURE/stdin-$count"
    if grep -q SOURCE_FAILURE "$MYSQL_FIXTURE_CAPTURE/stdin-$count"; then
      exit 19
    fi
    ;;
esac
""",
        encoding="utf-8",
    )
    mysqldump = directory / "mysqldump"
    mysqldump.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" > "$MYSQL_FIXTURE_CAPTURE/dump-args"
printf '%s\\n' 'fixture logical dump'
""",
        encoding="utf-8",
    )
    mysql.chmod(0o755)
    mysqldump.chmod(0o755)
    return capture


def fixture_environment(bin_dir, capture, version="8.4.5"):
    os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    os.environ["MYSQL_FIXTURE_CAPTURE"] = str(capture)
    os.environ["MYSQL_FIXTURE_VERSION"] = version
    os.environ["DATABASE_RECOVERY_SOCKET"] = "/run/mysql-fixture.sock"


def server():
    return MYSQL_RECOVERY.Server("mysql")


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def check_contract():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/mysql.yml").read_text(encoding="utf-8")
    )["mysql_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2
    assert defaults["routing"]["proxy"] == "none"
    assert defaults["routing"]["identity"] == "none"

    main = task_tree(ROLE / "tasks/main.yml")
    release_guard = task_named(main, "MySQL | Require a supported Debian release")
    assert "ansible_distribution == 'Debian'" in str(release_guard["ansible.builtin.assert"]["that"])
    assert "['bookworm', 'trixie']" in str(release_guard["ansible.builtin.assert"]["that"])
    repo_prereqs = task_named(main, "MySQL | Install APT repository prerequisites")
    assert repo_prereqs["ansible.builtin.apt"]["name"] == ["ca-certificates", "gnupg"]
    assert repo_prereqs["ansible.builtin.apt"]["update_cache"] is False
    repo_config = task_named(main, "MySQL | Configure Oracle MySQL 8.4 LTS APT repository")
    assert main.index(repo_prereqs) < main.index(repo_config)
    assert "{{ ansible_distribution_release }}" in repo_config["ansible.builtin.apt_repository"]["repo"]

    assert defaults["app"]["data_path"] == "/var/lib/mysql"
    initialize = task_named(main, "MySQL | Initialize an empty data directory")
    root_login = task_named(main, "MySQL | Give a newly initialized root the socket login")
    for task in (initialize, root_login):
        assert "not _mysql_system_schema.stat.exists" in str(task["when"])
    assert "IDENTIFIED WITH auth_socket" in str(root_login["ansible.builtin.command"]["argv"])
    assert (main.index(initialize) < main.index(task_named(main, "MySQL | Enable and start service"))
            < main.index(root_login))
    pbs_install = task_named(main, "MySQL | Install the pinned PBS client when this lab declares app backups")
    assert pbs_install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-install.yml")
    helper_install = task_named(main, "MySQL | Install the shared server recovery script")
    assert helper_install["ansible.builtin.include_tasks"].endswith(
        "tasks/database/install-server-recovery.yml"
    )

    backup = task_tree(ROLE / "tasks/backup.yml")
    assert task_named(backup, "MySQL backup | Create private staging space")[
        "ansible.builtin.tempfile"]["path"] == "/var/tmp"
    upload = task_named(backup, "MySQL backup | Upload the logical archive to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "native"
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "mysql.pxar", "path": "{{ _mysql_backup_tmp.path }}"}
    ]
    assert task_named(backup, "MySQL backup | Remove private staging data")

    restore = task_tree(ROLE / "tasks/restore.yml")
    preview = task_named(restore, "MySQL restore | List or verify the recovery point without changing MySQL")
    assert preview["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert preview["vars"]["pbs_client_archives"] == [{"name": "mysql.pxar"}]
    assert task_named(restore, "MySQL restore | Require the existing-target point to belong to its backup group")
    safety = task_named(restore, "MySQL restore | Download the pre-restore safety archive")
    assert safety["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert safety["vars"]["pbs_client_snapshot"] == "{{ _mysql_restore_prepoint }}"
    assert safety["vars"]["pbs_client_archives"][0]["path"].endswith("/safety")
    importer = task_named(restore, "MySQL restore | Import the point and retain rollback state until verified")
    assert "database_server_recovery_script, 'mysql', 'restore'" in str(
        importer["ansible.builtin.command"]["argv"]
    )
    assert task_named(restore, "MySQL restore | Remove private staging data")

    remove = task_tree(ROOT / "ansible/playbooks/apps/remove.yml")
    stop_services = task_named(remove, "Stop and disable every service unit")
    missing_unit_handling = str(stop_services["failed_when"])
    assert "could not find the requested service" in missing_unit_handling
    already_removed = task_named(remove, "Report an already-removed service")
    assert "could not find the requested service" in str(already_removed["when"])
    destruction_request = task_named(remove, "Validate the explicit guest destruction request before unwiring")
    destruction_guards = destruction_request["ansible.builtin.assert"]["that"]
    assert "_rm_database_backend_missing_guest" in str(destruction_guards)
    assert "_rm_destroy_guest_host | length == 0 or _rm_destroy_guest_host == instance" in str(destruction_guards)
    ownership_guard = task_named(remove, "Verify ownership and non-shared tags before unwiring")
    assert "_rm_destroy_guest_host | length > 0" in str(ownership_guard["when"])
    assert "_+lab" in str(ownership_guard["ansible.builtin.assert"]["that"])
    assert task_named(remove, "Destroy the explicitly selected native LXC")
    destroy = task_tree(ROOT / "ansible/tasks/proxmox/destroy-owned-lxc.yml")
    live_ownership_guard = task_named(destroy, "Destroy owned LXC | Require current lab ownership and an unshared guest")
    assert live_ownership_guard.get("no_log") is not True
    assert "Refusing to destroy the native guest" in live_ownership_guard["ansible.builtin.assert"]["fail_msg"]
    assert task_named(destroy, "Destroy owned LXC | Verify the guest is absent from the cluster")


def bundle(path, dump, version="8.4.5"):
    """A point in the format taken before the shared script: mysql-version.txt, no manifest."""
    path.mkdir(mode=0o700)
    (path / "mysql.sql").write_text(dump, encoding="utf-8")
    (path / "mysql-version.txt").write_text(version + "\n", encoding="utf-8")
    (path / "mysql.sql").chmod(0o600)
    (path / "mysql-version.txt").chmod(0o600)


def check_private_consistent_backup():
    with tempfile.TemporaryDirectory(prefix="homelab-mysql-recovery-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_mysql_stubs(bin_dir)
        fixture_environment(bin_dir, capture)
        staging = root / "staging"
        staging.mkdir(mode=0o755)

        MYSQL_RECOVERY.backup(server(), staging)

        dump = staging / "mysql.sql"
        manifest = staging / "mysql-manifest.json"
        assert dump.read_text(encoding="utf-8") == "fixture logical dump\n"
        assert MYSQL_RECOVERY.read_bundle(server(), staging) == (dump, "8.4.5")
        assert mode(staging) == 0o700
        assert mode(dump) == 0o600
        assert mode(manifest) == 0o600
        assert "--set-gtid-purged=OFF" in (capture / "dump-args").read_text(encoding="utf-8")
        arguments = (capture / "dump-args").read_text(encoding="utf-8")
        for option in [
            "--all-databases",
            "--lock-all-tables",
            "--routines",
            "--events",
            "--triggers",
            "--flush-privileges",
        ]:
            assert option in arguments
        assert "/run/mysql-fixture.sock" in arguments


def check_restore_and_rollback():
    with tempfile.TemporaryDirectory(prefix="homelab-mysql-restore-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_mysql_stubs(bin_dir)
        fixture_environment(bin_dir, capture)
        source = root / "source"
        safety = root / "safety"
        bundle(source, "SOURCE_FAILURE\n")
        bundle(safety, "safety recovery dump\n")

        try:
            MYSQL_RECOVERY.restore(server(), source, safety, "/var/lib/mysql", "mysql")
        except MYSQL_RECOVERY.RecoveryError as error:
            assert "pre-restore point was reapplied" in str(error)
        else:
            raise AssertionError("a failed selected import did not report rollback")

        inputs = sorted(capture.glob("stdin-*"), key=lambda path: int(path.name.split("-")[-1]))
        assert any("SOURCE_FAILURE" in path.read_text(encoding="utf-8") for path in inputs)
        assert any("safety recovery dump" in path.read_text(encoding="utf-8") for path in inputs)


def check_version_guard():
    with tempfile.TemporaryDirectory(prefix="homelab-mysql-version-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_mysql_stubs(bin_dir)
        fixture_environment(bin_dir, capture, version="8.4.5")
        source = root / "source"
        bundle(source, "source dump\n", version="8.3.2")

        try:
            MYSQL_RECOVERY.restore(server(), source, None, "/var/lib/mysql", "mysql")
        except MYSQL_RECOVERY.RecoveryError as error:
            assert "major/minor versions do not match" in str(error)
        else:
            raise AssertionError("a cross-minor recovery import was allowed")
        assert not list(capture.glob("stdin-*"))


def main():
    check_contract()
    check_private_consistent_backup()
    check_restore_and_rollback()
    check_version_guard()
    print("MySQL native recovery contract and reload restore checks passed.")


if __name__ == "__main__":
    main()
