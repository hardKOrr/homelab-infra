#!/usr/bin/env python3
"""Create and apply an application-consistent MySQL recovery point."""

from pathlib import Path
import os
import re
import subprocess
import sys


SOCKET = os.environ.get("MYSQL_RECOVERY_SOCKET", "/var/run/mysqld/mysqld.sock")
MYSQL = [
    "mysql",
    "--protocol=socket",
    "--user=root",
    f"--socket={SOCKET}",
    "--batch",
    "--skip-column-names",
    "--silent",
]


class RecoveryError(RuntimeError):
    """A concise error that does not expose database contents or credentials."""


def run_mysql(*, query=None, stdin=None, input_bytes=None, binary_mode=False):
    command = list(MYSQL)
    if binary_mode:
        command.append("--binary-mode")
    if query is not None:
        command.extend(["--execute", query])
    try:
        result = subprocess.run(
            command,
            stdin=stdin,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise RecoveryError("could not run the local MySQL client") from error
    if result.returncode:
        raise RecoveryError(f"mysql client failed (exit {result.returncode})")
    return result.stdout


def server_version():
    output = run_mysql(query="SELECT VERSION()")
    version = output.decode("utf-8", errors="strict").strip()
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise RecoveryError("could not read the MySQL server version")
    return version


def version_family(version):
    match = re.match(r"^(\d+\.\d+)", version)
    if not match:
        raise RecoveryError("recovery point has an invalid MySQL server version")
    return match.group(1)


def write_private(path, contents):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(contents)
    os.chmod(path, 0o600)


def backup(directory):
    target = Path(directory)
    if target.is_symlink() or not target.is_dir():
        raise RecoveryError("backup staging path is not a real directory")
    if any(target.iterdir()):
        raise RecoveryError("backup staging directory is not empty")
    os.chmod(target, 0o700)

    version = server_version()
    dump_path = target / "mysql.sql"
    command = [
        "mysqldump",
        "--protocol=socket",
        "--user=root",
        f"--socket={SOCKET}",
        "--all-databases",
        # The global read lock covers transactional and nontransactional tables and
        # includes MySQL's grant tables in the same recovery point as app databases.
        "--lock-all-tables",
        "--routines",
        "--events",
        "--triggers",
        "--hex-blob",
        "--flush-privileges",
        "--set-gtid-purged=OFF",
        "--skip-dump-date",
    ]
    try:
        with dump_path.open("xb") as output:
            os.chmod(dump_path, 0o600)
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
            )
    except OSError as error:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError("could not create the MySQL logical dump") from error
    if result.returncode:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError(f"mysqldump failed (exit {result.returncode})")
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError("mysqldump produced an empty recovery file")
    write_private(target / "mysql-version.txt", f"{version}\n".encode("utf-8"))


def read_bundle(directory):
    root = Path(directory)
    dump_path = root / "mysql.sql"
    version_path = root / "mysql-version.txt"
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("recovery archive is not a real directory")
    for path in (dump_path, version_path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise RecoveryError("recovery archive is missing a required MySQL file")
    version = version_path.read_text(encoding="utf-8").strip()
    version_family(version)
    return dump_path, version


def reset_application_databases():
    query = """
SELECT CONCAT('DROP DATABASE IF EXISTS `', REPLACE(SCHEMA_NAME, '`', '``'), '`;')
FROM INFORMATION_SCHEMA.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys')
ORDER BY SCHEMA_NAME
"""
    drop_statements = run_mysql(query=query)
    if drop_statements.strip():
        run_mysql(input_bytes=drop_statements)


def import_bundle(directory):
    dump_path, _ = read_bundle(directory)
    reset_application_databases()
    try:
        with dump_path.open("rb") as dump:
            run_mysql(stdin=dump, binary_mode=True)
    except OSError as error:
        raise RecoveryError("could not read the MySQL recovery file") from error
    run_mysql(query="FLUSH PRIVILEGES")
    run_mysql(query="SELECT 1")


def restore(source_directory, safety_directory=None):
    source_dump, source_version = read_bundle(source_directory)
    safety = None
    safety_version = None
    if safety_directory:
        if Path(source_directory).resolve() == Path(safety_directory).resolve():
            raise RecoveryError("selected and pre-restore recovery points must be separate")
        safety, safety_version = read_bundle(safety_directory)

    target_version = server_version()
    if version_family(source_version) != version_family(target_version):
        raise RecoveryError("recovery point and target MySQL major/minor versions do not match")
    if safety_version and version_family(safety_version) != version_family(target_version):
        raise RecoveryError("pre-restore point and target MySQL major/minor versions do not match")

    try:
        import_bundle(Path(source_directory))
    except RecoveryError:
        if safety is None:
            raise
        try:
            import_bundle(Path(safety_directory))
        except RecoveryError as rollback_error:
            raise RecoveryError(
                "selected recovery point failed and the pre-restore point could not be applied"
            ) from rollback_error
        raise RecoveryError(
            "selected recovery point failed; the pre-restore point was reapplied"
        ) from None

    print(f"Restored MySQL recovery point from server version {source_version}.")


def main(argv):
    if len(argv) == 3 and argv[1] == "backup":
        backup(argv[2])
        return 0
    if len(argv) in (3, 4) and argv[1] == "restore":
        restore(argv[2], argv[3] if len(argv) == 4 else None)
        return 0
    raise RecoveryError("usage: mysql-recovery.py backup <dir> | restore <source-dir> [<safety-dir>]")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoveryError as error:
        print(f"MySQL recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
