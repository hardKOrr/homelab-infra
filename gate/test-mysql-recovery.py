#!/usr/bin/env python3
"""Check MySQL's declared PBS contract and exercise its native recovery helper."""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import stat
import tempfile
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/mysql"
HELPER_PATH = ROLE / "files/mysql-recovery.py"
SPEC = spec_from_file_location("mysql_recovery", HELPER_PATH)
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
    os.environ["MYSQL_RECOVERY_SOCKET"] = "/run/mysql-fixture.sock"
    MYSQL_RECOVERY.SOCKET = os.environ["MYSQL_RECOVERY_SOCKET"]


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
    pbs_install = task_named(main, "MySQL | Install the PBS backup client")
    assert pbs_install["ansible.builtin.apt"]["name"] == "proxmox-backup-client"
    helper_install = task_named(main, "MySQL | Install the native recovery helper")
    assert helper_install["ansible.builtin.copy"]["src"] == "mysql-recovery.py"
    assert helper_install["ansible.builtin.copy"]["mode"] == "0750"

    backup = task_tree(ROLE / "tasks/backup.yml")
    assert any(
        task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml")
        for task in backup
    )
    upload = task_named(backup, "MySQL backup | Upload the logical archive to PBS")
    assert "mysql.pxar:" in str(upload["ansible.builtin.command"]["argv"])
    assert upload.get("no_log") is True
    assert task_named(backup, "MySQL backup | Require a new recovery point")
    assert task_named(backup, "MySQL backup | Verify that PBS stored the logical archive")
    assert task_named(backup, "MySQL backup | Remove private staging data")

    restore = task_tree(ROLE / "tasks/restore.yml")
    assert any(
        task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml")
        for task in restore
    )
    assert task_named(restore, "MySQL restore | Require the source point to belong to its backup group")
    assert task_named(restore, "MySQL restore | Require the existing-target point to belong to its backup group")
    assert task_named(restore, "MySQL restore | Verify the independent pre-restore archive")
    importer = task_named(restore, "MySQL restore | Import the point and retain rollback state until verified")
    assert "homelab-mysql-recovery" in str(importer["ansible.builtin.command"]["argv"])
    assert task_named(restore, "MySQL restore | Remove private staging data")


def bundle(path, dump, version="8.4.5"):
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

        MYSQL_RECOVERY.backup(staging)

        dump = staging / "mysql.sql"
        version = staging / "mysql-version.txt"
        assert dump.read_text(encoding="utf-8") == "fixture logical dump\n"
        assert version.read_text(encoding="utf-8") == "8.4.5\n"
        assert mode(staging) == 0o700
        assert mode(dump) == 0o600
        assert mode(version) == 0o600
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
            MYSQL_RECOVERY.restore(source, safety)
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
            MYSQL_RECOVERY.restore(source)
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
    print("MySQL native recovery contract and helper checks passed.")


if __name__ == "__main__":
    main()
