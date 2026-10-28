#!/usr/bin/env python3
"""Back up and restore a whole MariaDB or MySQL backend guest as one logical point.

A backend guest's own recovery point: every schema plus the grant tables, taken under the
global read lock as root over the local socket. One application's named database is
database-client.sh's job, not this file's. install-server-recovery.yml installs it; the
mariadb and mysql roles' backup.yml and restore.yml run it.

usage: server-recovery.py ENGINE backup DIR
       server-recovery.py ENGINE verify DIR
       server-recovery.py ENGINE restore [--data-dir D] [--service S] SOURCE [SAFETY]

backup   Writes ENGINE.sql and ENGINE-manifest.json (version, size, sha256) into an
         empty DIR.
verify   Checks a downloaded point's manifest and that its major.minor matches the server.
restore  Replaces the server's contents with SOURCE, by engine:
         mariadb  stops the service, moves the data directory aside, initializes a new
                  one, loads SOURCE; on any failure puts the original directory back.
         mysql    drops every non-system schema and loads SOURCE; on failure loads SAFETY
                  (the pre-restore point) instead.
         A MySQL point taken before this file existed carries mysql-version.txt in place
         of the manifest; it still restores.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


ENGINES = {
    "mariadb": {
        "label": "MariaDB",
        "client": "mariadb",
        "dump": "mariadb-dump",
        "socket": "/run/mysqld/mysqld.sock",
        "dump_flags": [],
        "restore": "rebuild",
    },
    "mysql": {
        "label": "MySQL",
        "client": "mysql",
        "dump": "mysqldump",
        "socket": "/var/run/mysqld/mysqld.sock",
        "dump_flags": ["--set-gtid-purged=OFF"],
        "restore": "reload",
    },
}
SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")


class RecoveryError(RuntimeError):
    """A concise recovery failure that never prints database contents."""


class Server:
    def __init__(self, engine):
        self.engine = engine
        self.spec = ENGINES[engine]
        self.label = self.spec["label"]
        self.socket = os.environ.get("DATABASE_RECOVERY_SOCKET", self.spec["socket"])

    def connection(self, program):
        return [program, "--protocol=socket", "--user=root", f"--socket={self.socket}"]

    def client(self, *, query=None, dump=None, input_bytes=None):
        command = self.connection(self.spec["client"]) + [
            "--batch",
            "--skip-column-names",
            "--silent",
        ]
        description = f"the local {self.label} client"
        if dump is not None:
            command.append("--binary-mode")
            try:
                with Path(dump).open("rb") as source:
                    return run_checked(command, description, stdin=source)
            except OSError as error:
                raise RecoveryError(f"could not read the {self.label} recovery export") from error
        if query is not None:
            command.extend(["--execute", query])
        return run_checked(command, description, input_bytes=input_bytes)

    def version(self):
        version = self.client(query="SELECT VERSION()").decode("utf-8", errors="strict").strip()
        if not re.match(r"^\d+\.\d+\.\d+", version):
            raise RecoveryError(f"could not read the {self.label} server version")
        return version

    def family(self, version):
        match = re.match(r"^(\d+\.\d+)", version) if isinstance(version, str) else None
        if not match:
            raise RecoveryError(f"recovery point has an invalid {self.label} server version")
        return match.group(1)

    def check_health(self):
        if self.client(query="SELECT 1").decode("utf-8", errors="strict").strip() != "1":
            raise RecoveryError(f"{self.label} did not pass its local health check")

    def load(self, dump_path):
        self.client(dump=dump_path)
        self.client(query="FLUSH PRIVILEGES")
        self.check_health()


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


def backup(server, directory):
    target = Path(directory)
    if target.is_symlink() or not target.is_dir():
        raise RecoveryError("backup staging path is not a real directory")
    if any(target.iterdir()):
        raise RecoveryError("backup staging directory is not empty")
    os.chmod(target, 0o700)

    version = server.version()
    dump_path = target / f"{server.engine}.sql"
    command = server.connection(server.spec["dump"]) + [
        "--all-databases",
        # The global read lock keeps every schema and the grant tables at one point.
        "--lock-all-tables",
        "--routines",
        "--events",
        "--triggers",
        "--hex-blob",
        "--flush-privileges",
        "--skip-dump-date",
        *server.spec["dump_flags"],
    ]
    try:
        with dump_path.open("xb") as output:
            os.chmod(dump_path, 0o600)
            result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError(f"could not create the {server.label} logical export") from error
    if result.returncode:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError(f"{server.spec['dump']} failed (exit {result.returncode})")
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        dump_path.unlink(missing_ok=True)
        raise RecoveryError(f"{server.spec['dump']} produced an empty recovery file")

    manifest = {
        "format": f"{server.engine}-native-v1",
        "server_version": version,
        "dump_file": dump_path.name,
        "dump_size_bytes": dump_path.stat().st_size,
        "dump_sha256": sha256(dump_path),
    }
    write_private(
        target / f"{server.engine}-manifest.json",
        (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"),
    )


def real_file(path):
    return not path.is_symlink() and path.is_file() and path.stat().st_size > 0


def read_bundle(server, directory):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("recovery archive is not a real directory")
    dump_path = root / f"{server.engine}.sql"
    manifest_path = root / f"{server.engine}-manifest.json"
    legacy_version_path = root / f"{server.engine}-version.txt"
    if not real_file(dump_path):
        raise RecoveryError(f"recovery archive is missing a required {server.label} file")

    if server.engine == "mysql" and not manifest_path.exists() and real_file(legacy_version_path):
        version = legacy_version_path.read_text(encoding="utf-8").strip()
        server.family(version)
        return dump_path, version

    if not real_file(manifest_path):
        raise RecoveryError(f"recovery archive is missing a required {server.label} file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"recovery archive has an invalid {server.label} manifest") from error
    if not isinstance(manifest, dict):
        raise RecoveryError(f"recovery archive has an invalid {server.label} manifest")
    version = manifest.get("server_version", "")
    server.family(version)
    if (
        manifest.get("format") != f"{server.engine}-native-v1"
        or manifest.get("dump_file") != dump_path.name
        or manifest.get("dump_size_bytes") != dump_path.stat().st_size
        or manifest.get("dump_sha256") != sha256(dump_path)
    ):
        raise RecoveryError(f"{server.label} recovery export failed its manifest check")
    return dump_path, version


def safe_service(server, service):
    if not re.match(r"^[A-Za-z0-9_.@-]+$", service):
        raise RecoveryError(f"the {server.label} service name is unsafe")
    return service


def service_action(server, action, service):
    run_checked(["systemctl", action, safe_service(server, service)], f"the {server.label} service {action}")


def ensure_data_directory(server, path):
    directory = Path(path).absolute()
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise RecoveryError(f"{server.label} data directory is unavailable") from error
    if not directory.is_dir() or directory.is_symlink():
        raise RecoveryError(f"{server.label} data directory must be a real directory")
    if not directory.parent.is_dir():
        raise RecoveryError(f"{server.label} data directory parent is unavailable")
    if metadata.st_dev != directory.parent.stat().st_dev:
        raise RecoveryError(f"{server.label} data directory cannot be moved on one filesystem")
    return directory


def rebuild(server, source_dump, source_version, data_directory, service):
    """Load into a freshly initialized data directory; put the original back on failure."""
    safe_service(server, service)
    data_dir = ensure_data_directory(server, data_directory)
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if active.returncode:
        raise RecoveryError(f"{server.label} must be running before its data can be replaced")

    rollback_dir = data_dir.parent / f".{data_dir.name}.homelab-restore-{uuid.uuid4().hex}"
    moved_original = False
    try:
        service_action(server, "stop", service)
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
            f"{server.label} system database initialization",
        )
        service_action(server, "start", service)
        server.load(source_dump)
        if server.family(server.version()) != server.family(source_version):
            raise RecoveryError(f"restored {server.label} server version did not verify")
    except (RecoveryError, OSError) as error:
        rollback_error = None
        try:
            if moved_original:
                service_action(server, "stop", service)
                if data_dir.is_symlink():
                    data_dir.unlink()
                elif data_dir.exists():
                    shutil.rmtree(data_dir)
                os.replace(rollback_dir, data_dir)
                service_action(server, "start", service)
                server.version()
                server.check_health()
            else:
                service_action(server, "start", service)
        except (OSError, RecoveryError) as rollback_failure:
            rollback_error = rollback_failure
        if rollback_error:
            raise RecoveryError(
                f"{server.label} restore failed and the original data directory could not be verified"
            ) from rollback_error
        raise RecoveryError(
            f"{server.label} restore failed; the original data directory was restored"
        ) from error

    try:
        shutil.rmtree(rollback_dir)
    except OSError as error:
        raise RecoveryError(
            f"{server.label} restore succeeded but the retained original data directory "
            "could not be removed"
        ) from error


def reset_application_databases(server):
    placeholders = ", ".join(f"'{name}'" for name in SYSTEM_SCHEMAS)
    statements = server.client(
        query=(
            "SELECT CONCAT('DROP DATABASE IF EXISTS `', REPLACE(SCHEMA_NAME, '`', '``'), '`;') "
            f"FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME NOT IN ({placeholders}) "
            "ORDER BY SCHEMA_NAME"
        )
    )
    if statements.strip():
        server.client(input_bytes=statements)


def reload(server, source_dump, safety_dump):
    """Drop every application schema and load the point; load the safety point on failure."""
    try:
        reset_application_databases(server)
        server.load(source_dump)
    except RecoveryError:
        if safety_dump is None:
            raise
        try:
            reset_application_databases(server)
            server.load(safety_dump)
        except RecoveryError as rollback_error:
            raise RecoveryError(
                "selected recovery point failed and the pre-restore point could not be applied"
            ) from rollback_error
        raise RecoveryError(
            "selected recovery point failed; the pre-restore point was reapplied"
        ) from None


def restore(server, source_directory, safety_directory, data_directory, service):
    source_dump, source_version = read_bundle(server, source_directory)
    safety_dump = safety_version = None
    if safety_directory:
        if Path(source_directory).resolve() == Path(safety_directory).resolve():
            raise RecoveryError("selected and pre-restore recovery points must be separate")
        safety_dump, safety_version = read_bundle(server, safety_directory)

    target_family = server.family(server.version())
    if server.family(source_version) != target_family:
        raise RecoveryError(
            f"recovery point and target {server.label} major/minor versions do not match"
        )
    if safety_version and server.family(safety_version) != target_family:
        raise RecoveryError(
            f"pre-restore point and target {server.label} major/minor versions do not match"
        )

    if server.spec["restore"] == "rebuild":
        rebuild(server, source_dump, source_version, data_directory, service)
    else:
        reload(server, source_dump, safety_dump)
    print(f"Restored {server.label} recovery point from server version {source_version}.")


def parse(argv):
    parser = argparse.ArgumentParser(prog="server-recovery.py")
    parser.add_argument("engine", choices=sorted(ENGINES))
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("backup").add_argument("directory")
    actions.add_parser("verify").add_argument("directory")
    restore_parser = actions.add_parser("restore")
    restore_parser.add_argument("--data-dir", default="/var/lib/mysql")
    restore_parser.add_argument("--service")
    restore_parser.add_argument("source")
    restore_parser.add_argument("safety", nargs="?")
    return parser.parse_args(argv)


def main(argv):
    args = parse(argv[1:])
    server = Server(args.engine)
    if args.action == "backup":
        backup(server, args.directory)
    elif args.action == "verify":
        read_bundle(server, args.directory)
        server.family(server.version())
    else:
        restore(server, args.source, args.safety, args.data_dir, args.service or args.engine)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoveryError as error:
        print(f"Database server recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
