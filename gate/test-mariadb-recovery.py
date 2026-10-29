#!/usr/bin/env python3
"""Check MariaDB's native PBS contract and the shared server recovery script's rebuild restore."""

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import stat
import tempfile
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/mariadb"
SPEC = spec_from_file_location(
    "server_recovery", ROOT / "ansible/tasks/database/scripts/server-recovery.py"
)
MARIADB_RECOVERY = module_from_spec(SPEC)
SPEC.loader.exec_module(MARIADB_RECOVERY)


def tasks(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks(value.get(key, []))


def named(items, name):
    return next(item for item in items if item.get("name") == name)


def executable(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def install_stubs(bin_dir, capture, data_dir):
    bin_dir.mkdir()
    capture.mkdir()
    executable(
        bin_dir / "mariadb",
        """#!/bin/sh
case "$*" in
  *"SELECT VERSION()"*) printf '%s\\n' "$MARIADB_FIXTURE_VERSION" ;;
  *"SELECT 1"*) printf '1\\n' ;;
  *"FLUSH PRIVILEGES"*) : ;;
  *)
    cat > "$MARIADB_FIXTURE_CAPTURE/import.sql"
    if grep -q IMPORT_FAILURE "$MARIADB_FIXTURE_CAPTURE/import.sql"; then exit 19; fi
    printf 'restored\\n' > "$MARIADB_FIXTURE_DATA_DIR/imported"
    ;;
esac
""",
    )
    executable(
        bin_dir / "mariadb-dump",
        """#!/bin/sh
printf '%s\\n' "$*" > "$MARIADB_FIXTURE_CAPTURE/dump-args"
printf '%s\\n' '-- MariaDB dump fixture' 'CREATE DATABASE fixture;'
""",
    )
    executable(
        bin_dir / "systemctl",
        """#!/bin/sh
case "$1" in
  is-active) test -f "$MARIADB_FIXTURE_CAPTURE/active" ;;
  stop) rm -f "$MARIADB_FIXTURE_CAPTURE/active"; printf 'stop\\n' >> "$MARIADB_FIXTURE_CAPTURE/service.log" ;;
  start) touch "$MARIADB_FIXTURE_CAPTURE/active"; printf 'start\\n' >> "$MARIADB_FIXTURE_CAPTURE/service.log" ;;
  *) exit 2 ;;
esac
""",
    )
    executable(
        bin_dir / "mariadb-install-db",
        """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    --datadir=*) dir=$(printf '%s' "$arg" | sed 's/^--datadir=//') ;;
  esac
done
mkdir -p "$dir"
printf 'fresh\\n' > "$dir/initialized"
""",
    )
    os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    os.environ["MARIADB_FIXTURE_CAPTURE"] = str(capture)
    os.environ["MARIADB_FIXTURE_DATA_DIR"] = str(data_dir)
    os.environ["MARIADB_FIXTURE_VERSION"] = "10.11.6-MariaDB"
    os.environ["DATABASE_RECOVERY_SOCKET"] = "/run/mariadb-fixture.sock"


def server():
    return MARIADB_RECOVERY.Server("mariadb")


def bundle(directory, sql, version="10.11.6-MariaDB"):
    directory.mkdir()
    dump = directory / "mariadb.sql"
    dump.write_text(sql, encoding="utf-8")
    manifest = {
        "format": "mariadb-native-v1",
        "server_version": version,
        "dump_file": dump.name,
        "dump_size_bytes": dump.stat().st_size,
        "dump_sha256": MARIADB_RECOVERY.sha256(dump),
    }
    (directory / "mariadb-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def check_contract():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/mariadb.yml").read_text(encoding="utf-8")
    )["mariadb_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["routing"]["proxy"] == defaults["routing"]["identity"] == "none"

    main = list(tasks(yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))))
    packages = named(main, "MariaDB | Install server and Python client")
    assert packages["ansible.builtin.apt"]["name"] == [
        "mariadb-server", "mariadb-client", "python3-pymysql"
    ]
    helper = named(main, "MariaDB | Install the shared server recovery script")
    assert helper["ansible.builtin.include_tasks"].endswith("tasks/database/install-server-recovery.yml")
    pbs = named(main, "MariaDB | Install the pinned PBS client when this lab declares app backups")
    assert pbs["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-install.yml")

    backup = list(tasks(yaml.safe_load((ROLE / "tasks/backup.yml").read_text(encoding="utf-8"))))
    export = named(backup, "MariaDB backup | Export all databases and grants consistently")
    assert export["ansible.builtin.command"]["argv"][-3:] == [
        "mariadb", "backup", "{{ _mariadb_backup_tmp.path }}"
    ]
    staging = named(backup, "MariaDB backup | Create private staging space")
    assert staging["ansible.builtin.tempfile"]["path"] == "/var/tmp"
    upload = named(backup, "MariaDB backup | Upload the logical archive to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "native"
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "mariadb.pxar", "path": "{{ _mariadb_backup_tmp.path }}"}
    ]

    restore = list(tasks(yaml.safe_load((ROLE / "tasks/restore.yml").read_text(encoding="utf-8"))))
    preview = named(restore, "MariaDB restore | List or verify the recovery point without changing MariaDB")
    assert preview["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert preview["vars"]["pbs_client_archives"] == [{"name": "mariadb.pxar"}]
    for name, staging in [("Download the selected recovery archive", "source"),
                          ("Download the pre-restore safety archive", "safety")]:
        download = named(restore, f"MariaDB restore | {name}")
        assert download["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
        assert download["vars"]["pbs_client_archives"][0]["path"].endswith(f"/{staging}")
    safety = named(restore, "MariaDB restore | Download the pre-restore safety archive")
    assert safety["vars"]["pbs_client_snapshot"] == "{{ _mariadb_restore_prepoint }}"
    assert safety["vars"]["pbs_client_backup_id"] == "{{ _mariadb_restore_target_backup_id }}"
    assert named(restore, "MariaDB restore | Create private restore staging space")[
        "ansible.builtin.tempfile"]["path"] == "/var/tmp"
    apply_restore = named(
        restore,
        "MariaDB restore | Import the point and retain rollback state until verified",
    )
    command = apply_restore["ansible.builtin.command"]["argv"]
    assert "app_config.app.data_path" in command
    assert "app_config.app.service_name" in command
    assert "_mariadb_restore_tmp.path ~ '/safety'" in command

    for filename, task_name in [
        ("backup-app.yml", "Resolve the declared native recovery capability"),
        ("restore-app.yml", "Resolve the declared recovery capability"),
    ]:
        play = yaml.safe_load(
            (ROOT / "ansible/playbooks/maintenance" / filename).read_text(encoding="utf-8")
        )
        dispatch = named(list(tasks(play)), task_name)
        assert "../../tasks/recovery/resolve-method.yml" in dispatch["ansible.builtin.include_tasks"]


def check_backup_bundle():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "capture"
        install_stubs(root / "bin", capture, root / "data")
        staging = root / "staging"
        staging.mkdir()
        MARIADB_RECOVERY.backup(server(), staging)

        args = (capture / "dump-args").read_text(encoding="utf-8")
        assert "--protocol=socket" in args and "--user=root" in args
        assert "--all-databases" in args and "--lock-all-tables" in args
        assert all(flag in args for flag in ["--routines", "--events", "--triggers", "--flush-privileges"])
        dump, version = MARIADB_RECOVERY.read_bundle(server(), staging)
        assert version == "10.11.6-MariaDB"
        assert dump.read_text(encoding="utf-8").startswith("-- MariaDB dump")
        assert stat.S_IMODE(staging.stat().st_mode) == 0o700
        assert stat.S_IMODE(dump.stat().st_mode) == 0o600
        assert stat.S_IMODE((staging / "mariadb-manifest.json").stat().st_mode) == 0o600


def check_restore_and_rollback():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "capture"
        data_dir = root / "var/lib/mysql"
        data_dir.mkdir(parents=True)
        (data_dir / "original.marker").write_text("old", encoding="utf-8")
        install_stubs(root / "bin", capture, data_dir)
        (capture / "active").touch()
        source = root / "source"
        safety = root / "safety"
        bundle(source, "CREATE DATABASE canary;\\n")
        bundle(safety, "CREATE DATABASE fresh;\\n")

        MARIADB_RECOVERY.restore(server(), source, safety, data_dir, "mariadb")
        assert (data_dir / "imported").read_text(encoding="utf-8").strip() == "restored"
        assert not (data_dir / "original.marker").exists()
        assert (capture / "active").exists()
        assert not list(data_dir.parent.glob(".mysql.homelab-restore-*"))

        failed_data = root / "second-var/lib/mysql"
        failed_data.mkdir(parents=True)
        (failed_data / "original.marker").write_text("old", encoding="utf-8")
        os.environ["MARIADB_FIXTURE_DATA_DIR"] = str(failed_data)
        failed_source = root / "failed-source"
        bundle(failed_source, "IMPORT_FAILURE\\n")
        try:
            MARIADB_RECOVERY.restore(server(), failed_source, safety, failed_data, "mariadb")
        except MARIADB_RECOVERY.RecoveryError as error:
            assert "original data directory was restored" in str(error)
        else:
            raise AssertionError("failed MariaDB import was accepted")
        assert (failed_data / "original.marker").read_text(encoding="utf-8") == "old"
        assert not (failed_data / "imported").exists()
        assert (capture / "active").exists()
        assert not list(failed_data.parent.glob(".mysql.homelab-restore-*"))


def check_preflight_refusal():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "capture"
        data_dir = root / "var/lib/mysql"
        data_dir.mkdir(parents=True)
        (data_dir / "original.marker").write_text("old", encoding="utf-8")
        install_stubs(root / "bin", capture, data_dir)
        (capture / "active").touch()
        source = root / "source"
        bundle(source, "CREATE DATABASE canary;\\n")
        with (source / "mariadb.sql").open("a", encoding="utf-8") as dump:
            dump.write("changed")
        try:
            MARIADB_RECOVERY.restore(server(), source, None, data_dir, "mariadb")
        except MARIADB_RECOVERY.RecoveryError as error:
            assert "manifest check" in str(error)
        else:
            raise AssertionError("a modified MariaDB export was accepted")
        assert (data_dir / "original.marker").exists()
        assert not (capture / "service.log").exists()


def main():
    check_contract()
    check_backup_bundle()
    check_restore_and_rollback()
    check_preflight_refusal()
    print("MariaDB native export, PBS dispatch, integrity checks, and rebuild restore passed")


if __name__ == "__main__":
    main()
