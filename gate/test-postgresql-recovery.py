#!/usr/bin/env python3
"""Check PostgreSQL's native PBS contract and transactional recovery helper."""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import tempfile
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/postgresql"
HELPER_PATH = ROLE / "files/postgresql-recovery.py"
SPEC = spec_from_file_location("postgresql_recovery", HELPER_PATH)
POSTGRESQL_RECOVERY = module_from_spec(SPEC)
SPEC.loader.exec_module(POSTGRESQL_RECOVERY)


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


def write_stub(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def install_command_stubs(directory):
    directory.mkdir()
    capture = directory / "capture"
    capture.mkdir()
    write_stub(
        directory / "pg_verifybackup",
        "printf '%s\\n' \"$*\" >> \"$PG_RECOVERY_TEST_CAPTURE/verify.log\"\n"
        "[ \"${PG_RECOVERY_TEST_VERIFY_FAIL:-0}\" != 1 ] || exit 19\n",
    )
    write_stub(
        directory / "systemctl",
        "printf '%s\\n' \"$*\" >> \"$PG_RECOVERY_TEST_CAPTURE/systemctl.log\"\n",
    )
    write_stub(
        directory / "pg_isready",
        "count=0\n"
        "[ ! -f \"$PG_RECOVERY_TEST_CAPTURE/ready-count\" ] || count=$(cat \"$PG_RECOVERY_TEST_CAPTURE/ready-count\")\n"
        "count=$((count + 1))\n"
        "printf '%s\\n' \"$count\" > \"$PG_RECOVERY_TEST_CAPTURE/ready-count\"\n"
        "[ \"${PG_RECOVERY_TEST_FAIL_FIRST_READY:-0}\" != 1 ] || [ \"$count\" -gt 1 ] || exit 1\n",
    )
    write_stub(
        directory / "runuser",
        "printf '%s\\n' \"$*\" >> \"$PG_RECOVERY_TEST_CAPTURE/runuser.log\"\n"
        "printf '1\\n'\n",
    )
    return capture


def fixture_environment(bin_dir, capture, **variables):
    previous = {"PATH": os.environ.get("PATH"), "PG_RECOVERY_TEST_CAPTURE": os.environ.get("PG_RECOVERY_TEST_CAPTURE")}
    previous.update({key: os.environ.get(key) for key in variables})
    os.environ["PATH"] = f"{bin_dir}:{os.environ.get('PATH', '')}"
    os.environ["PG_RECOVERY_TEST_CAPTURE"] = str(capture)
    for key, value in variables.items():
        os.environ[key] = value
    return previous


def restore_environment(previous):
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def bundle(path, version="16"):
    cluster = path / "cluster"
    cluster.mkdir(parents=True)
    (cluster / "PG_VERSION").write_text(version + "\n", encoding="ascii")
    (cluster / "backup_manifest").write_text("fixture manifest\n", encoding="ascii")
    (cluster / "recovery-canary").write_text("selected recovery point\n", encoding="ascii")
    return cluster


def check_contract():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/postgresql.yml").read_text(encoding="utf-8")
    )["postgresql_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2
    assert defaults["proxmox"]["type"] == "lxc"
    assert defaults["routing"]["proxy"] == "none"
    assert defaults["routing"]["identity"] == "none"

    deploy = task_tree(ROOT / "ansible/playbooks/apps/postgresql.yml")
    assert task_named(deploy, "Provision PostgreSQL LXC")
    assert task_named(deploy, "Provision PostgreSQL VM")
    type_guard = task_named(deploy, "Require the existing PostgreSQL guest to match its configured type")
    assert "proxmox_vmtype" in str(type_guard["ansible.builtin.assert"]["that"])

    main = task_tree(ROLE / "tasks/main.yml")
    pbs_client = task_named(main, "PostgreSQL | Install the PBS backup client when backups are enabled")
    assert pbs_client["ansible.builtin.apt"]["name"] == "proxmox-backup-client"
    helper = task_named(main, "PostgreSQL | Install the native recovery helper when backups are enabled")
    assert helper["ansible.builtin.copy"]["src"] == "postgresql-recovery.py"
    assert helper["ansible.builtin.copy"]["mode"] == "0750"
    socket_access = task_named(main, "PostgreSQL | Permit native recovery through the local superuser socket")
    assert socket_access["ansible.builtin.lineinfile"]["line"] == "local replication postgres peer"
    assert socket_access["ansible.builtin.lineinfile"]["insertbefore"] == "BOF"

    backup = task_tree(ROLE / "tasks/backup.yml")
    exporter = task_named(backup, "PostgreSQL backup | Export the full cluster through the local superuser socket")
    export_args = exporter["ansible.builtin.command"]["argv"]
    assert "pg_basebackup" in export_args
    assert "--host=/var/run/postgresql" in export_args
    assert "--username=postgres" in export_args
    assert "--wal-method=stream" in export_args
    assert exporter["become_user"] == "postgres"
    assert task_named(backup, "PostgreSQL backup | Refuse unsupported external tablespaces")
    upload = task_named(backup, "PostgreSQL backup | Upload the physical archive to PBS")
    assert "postgresql.pxar:" in str(upload["ansible.builtin.command"]["argv"])
    assert upload["no_log"] is True
    assert task_named(backup, "PostgreSQL backup | Require a new recovery point")
    assert task_named(backup, "PostgreSQL backup | Verify that PBS stored the native archive")

    restore = task_tree(ROLE / "tasks/restore.yml")
    assert task_named(restore, "PostgreSQL restore | Require the source point to belong to its backup group")
    assert task_named(restore, "PostgreSQL restore | Require the independent existing-target point")
    assert task_named(restore, "PostgreSQL restore | Require the selected native archive")
    assert task_named(restore, "PostgreSQL restore | Require the pre-restore native archive")
    importer = task_named(restore, "PostgreSQL restore | Verify and apply the point transactionally")
    assert "homelab-postgresql-recovery" in str(importer["ansible.builtin.command"]["argv"])
    assert task_named(restore, "PostgreSQL restore | Remove private staging data")


def check_native_verification_and_version_guard():
    with tempfile.TemporaryDirectory(prefix="homelab-postgresql-verify-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_command_stubs(bin_dir)
        previous = fixture_environment(bin_dir, capture)
        try:
            source = root / "source"
            bundle(source)
            POSTGRESQL_RECOVERY.verify_bundle(source, "16")
            assert str(source / "cluster") in (capture / "verify.log").read_text(encoding="utf-8")

            try:
                POSTGRESQL_RECOVERY.verify_bundle(source, "17")
            except POSTGRESQL_RECOVERY.RecoveryError as error:
                assert "major versions do not match" in str(error)
            else:
                raise AssertionError("a cross-major recovery point was allowed")

            original_which = POSTGRESQL_RECOVERY.shutil.which
            original_run_checked = POSTGRESQL_RECOVERY.run_checked
            verify_commands = []

            def record_versioned_verifier(argv, message):
                verify_commands.append(argv)
                return ""

            POSTGRESQL_RECOVERY.shutil.which = lambda _name: None
            POSTGRESQL_RECOVERY.run_checked = record_versioned_verifier
            try:
                POSTGRESQL_RECOVERY.verify_bundle(source, "16")
            finally:
                POSTGRESQL_RECOVERY.shutil.which = original_which
                POSTGRESQL_RECOVERY.run_checked = original_run_checked
            assert verify_commands[0][0] == "/usr/lib/postgresql/16/bin/pg_verifybackup"

            (source / "cluster" / "backup_manifest").unlink()
            try:
                POSTGRESQL_RECOVERY.verify_bundle(source, "16")
            except POSTGRESQL_RECOVERY.RecoveryError as error:
                assert "required PostgreSQL metadata" in str(error)
            else:
                raise AssertionError("an archive without its native manifest was allowed")
        finally:
            restore_environment(previous)


def check_restore_and_rollback():
    with tempfile.TemporaryDirectory(prefix="homelab-postgresql-restore-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_command_stubs(bin_dir)
        previous = fixture_environment(bin_dir, capture)
        try:
            source = root / "source"
            source_cluster = bundle(source)
            target = root / "data"
            target.mkdir()
            (target / "original-state").write_text("keep on failure\n", encoding="utf-8")

            POSTGRESQL_RECOVERY.restore(source, target, "postgresql@16-main", 5432, "16")
            assert (target / "recovery-canary").read_text(encoding="utf-8") == "selected recovery point\n"
            assert not (target / "original-state").exists()
            assert not list(root.glob(".homelab-postgresql-rollback-*"))
            assert "stop postgresql@16-main" in (capture / "systemctl.log").read_text(encoding="utf-8")
            assert "start postgresql@16-main" in (capture / "systemctl.log").read_text(encoding="utf-8")
            assert "--host=/var/run/postgresql" in (capture / "runuser.log").read_text(encoding="utf-8")
            assert source_cluster.exists() is False
        finally:
            restore_environment(previous)

    with tempfile.TemporaryDirectory(prefix="homelab-postgresql-rollback-test.") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        capture = install_command_stubs(bin_dir)
        previous = fixture_environment(
            bin_dir,
            capture,
            PG_RECOVERY_TEST_FAIL_FIRST_READY="1",
        )
        try:
            source = root / "source"
            bundle(source)
            target = root / "data"
            target.mkdir()
            (target / "original-state").write_text("keep on failure\n", encoding="utf-8")

            try:
                POSTGRESQL_RECOVERY.restore(source, target, "postgresql@16-main", 5432, "16")
            except POSTGRESQL_RECOVERY.RecoveryError as error:
                assert "original cluster was restored and verified" in str(error)
            else:
                raise AssertionError("an unhealthy restored cluster was not rolled back")

            assert (target / "original-state").read_text(encoding="utf-8") == "keep on failure\n"
            assert not (target / "recovery-canary").exists()
            assert not list(root.glob(".homelab-postgresql-rollback-*"))
            assert (capture / "ready-count").read_text(encoding="utf-8").strip() == "2"
        finally:
            restore_environment(previous)


def main():
    check_contract()
    check_native_verification_and_version_guard()
    check_restore_and_rollback()
    print("PostgreSQL native recovery contract and helper checks passed.")


if __name__ == "__main__":
    main()
