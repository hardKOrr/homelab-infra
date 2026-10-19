#!/usr/bin/env python3
"""Create and transactionally restore a full MariaDB logical recovery point."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


SOCKET = os.environ.get("MARIADB_RECOVERY_SOCKET", "/run/mysqld/mysqld.sock")
CLIENT = [
    "mariadb",
    "--protocol=socket",
    "--user=root",
    f"--socket={SOCKET}",
    "--batch",
    "--skip-column-names",
    "--silent",
]


class RecoveryError(RuntimeError):
    """A concise recovery failure that never prints database contents."""


def run_checked(argv, description, *, stdin=None, input_bytes=None):
    try:
        result = subprocess.run(
            argv,
            stdin=stdin,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise RecoveryError(f"could not run {description}") from error
    if result.returncode:
        raise RecoveryError(f"{description} failed (exit {result.returncode})")
    return result.stdout


def run_client(*, query=None, dump=None):
    command = list(CLIENT)
    if dump is not None:
        command.append("--binary-mode")
        try:
            with Path(dump).open("rb") as source:
                return run_checked(command, "the local MariaDB client", stdin=source)
        except OSError as error:
            raise RecoveryError("could not read the MariaDB recovery export") from error
    if query is not None:
        command.extend(["--execute", query])
    return run_checked(command, "the local MariaDB client")


def server_version():
    output = run_client(query="SELECT VERSION()")
    version = output.decode("utf-8", errors="strict").strip()
    if not re.match(r"^\d+\.\d+\.\d+", version):
        raise RecoveryError("could not read the MariaDB server version")
    return version


def version_family(version):
    if not isinstance(version, str):
        raise RecoveryError("recovery point has an invalid MariaDB server version")
    match = re.match(r"^(\d+\.\d+)", version)
    if not match:
        raise RecoveryError("recovery point has an invalid MariaDB server version")
    return match.group(1)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    dump_path = target / "mariadb.sql"
    command = [
        "mariadb-dump",
        "--protocol=socket",
        "--user=root",
        f"--socket={SOCKET}",
        "--all-databases",
        # The global read lock keeps every schema and the grant tables at one point.
        "--lock-all-tables",
        "--routines",
        "--events",
        "--triggers",
        "--hex-blob",
        "--flush-privileges",
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
        raise RecoveryError("could not create the MariaDB logical export") from error
    if result.returncode:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError(f"mariadb-dump failed (exit {result.returncode})")
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError("mariadb-dump produced an empty recovery file")

    manifest = {
        "format": "mariadb-native-v1",
        "server_version": version,
        "dump_file": dump_path.name,
        "dump_size_bytes": dump_path.stat().st_size,
        "dump_sha256": sha256(dump_path),
    }
    write_private(
        target / "mariadb-manifest.json",
        (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"),
    )


def read_bundle(directory):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("recovery archive is not a real directory")
    dump_path = root / "mariadb.sql"
    manifest_path = root / "mariadb-manifest.json"
    for path in (dump_path, manifest_path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise RecoveryError("recovery archive is missing a required MariaDB file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError("recovery archive has an invalid MariaDB manifest") from error
    if not isinstance(manifest, dict):
        raise RecoveryError("recovery archive has an invalid MariaDB manifest")
    version = manifest.get("server_version", "")
    version_family(version)
    if (
        manifest.get("format") != "mariadb-native-v1"
        or manifest.get("dump_file") != dump_path.name
        or manifest.get("dump_size_bytes") != dump_path.stat().st_size
        or manifest.get("dump_sha256") != sha256(dump_path)
    ):
        raise RecoveryError("MariaDB recovery export failed its manifest check")
    return dump_path, version


def service_action(action, service):
    if not re.match(r"^[A-Za-z0-9_.@-]+$", service):
        raise RecoveryError("the MariaDB service name is unsafe")
    run_checked(["systemctl", action, service], f"the MariaDB service {action}")


def ensure_data_directory(path):
    directory = Path(path).absolute()
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise RecoveryError("MariaDB data directory is unavailable") from error
    if not directory.is_dir() or directory.is_symlink():
        raise RecoveryError("MariaDB data directory must be a real directory")
    if not directory.parent.is_dir():
        raise RecoveryError("MariaDB data directory parent is unavailable")
    if metadata.st_dev != directory.parent.stat().st_dev:
        raise RecoveryError("MariaDB data directory cannot be moved on one filesystem")
    return directory


def import_export(dump_path):
    run_client(dump=dump_path)
    run_client(query="FLUSH PRIVILEGES")
    result = run_client(query="SELECT 1").decode("utf-8", errors="strict").strip()
    if result != "1":
        raise RecoveryError("MariaDB did not pass its local health check after import")


def restore(source_directory, data_directory, service, safety_directory=None):
    source_dump, source_version = read_bundle(source_directory)
    if safety_directory:
        if Path(source_directory).resolve() == Path(safety_directory).resolve():
            raise RecoveryError("selected and pre-restore recovery points must be separate")
        _, safety_version = read_bundle(safety_directory)
    else:
        safety_version = None

    if not re.match(r"^[A-Za-z0-9_.@-]+$", service):
        raise RecoveryError("the MariaDB service name is unsafe")
    target_version = server_version()
    if version_family(source_version) != version_family(target_version):
        raise RecoveryError("recovery point and target MariaDB major/minor versions do not match")
    if safety_version and version_family(safety_version) != version_family(target_version):
        raise RecoveryError("pre-restore point and target MariaDB major/minor versions do not match")

    data_dir = ensure_data_directory(data_directory)
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if active.returncode:
        raise RecoveryError("MariaDB must be running before its data can be replaced")

    rollback_dir = data_dir.parent / f".{data_dir.name}.homelab-restore-{uuid.uuid4().hex}"
    moved_original = False
    try:
        service_action("stop", service)
        os.replace(data_dir, rollback_dir)
        moved_original = True
        run_checked(
            [
                "mariadb-install-db",
                "--user=mysql",
                f"--datadir={data_dir}",
                "--auth-root-authentication-method=socket",
                "--skip-test-db",
            ],
            "MariaDB system database initialization",
        )
        service_action("start", service)
        import_export(source_dump)
        restored_version = server_version()
        if version_family(restored_version) != version_family(source_version):
            raise RecoveryError("restored MariaDB server version did not verify")
    except (RecoveryError, OSError) as error:
        rollback_error = None
        if moved_original:
            try:
                service_action("stop", service)
                if data_dir.is_symlink():
                    data_dir.unlink()
                elif data_dir.exists():
                    shutil.rmtree(data_dir)
                os.replace(rollback_dir, data_dir)
                service_action("start", service)
                server_version()
                run_client(query="SELECT 1")
            except (OSError, RecoveryError) as rollback_failure:
                rollback_error = rollback_failure
        else:
            try:
                service_action("start", service)
            except RecoveryError as restart_failure:
                rollback_error = restart_failure
        if rollback_error:
            raise RecoveryError(
                "MariaDB restore failed and the original data directory could not be verified"
            ) from rollback_error
        raise RecoveryError(
            "MariaDB restore failed; the original data directory was restored"
        ) from error

    try:
        shutil.rmtree(rollback_dir)
    except OSError as error:
        raise RecoveryError(
            "MariaDB restore succeeded but the retained original data directory could not be removed"
        ) from error
    print(f"Restored MariaDB recovery point from server version {source_version}.")


def main(argv):
    if len(argv) == 3 and argv[1] == "backup":
        backup(argv[2])
        return 0
    if len(argv) == 3 and argv[1] == "verify":
        read_bundle(argv[2])
        version_family(server_version())
        return 0
    if len(argv) in (5, 6) and argv[1] == "restore":
        restore(
            argv[2],
            argv[3],
            argv[4],
            argv[5] if len(argv) == 6 else None,
        )
        return 0
    raise RecoveryError(
        "usage: mariadb-recovery.py backup <dir> | verify <dir> | "
        "restore <source-dir> <datadir> <service> [<safety-dir>]"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoveryError as error:
        print(f"MariaDB recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
