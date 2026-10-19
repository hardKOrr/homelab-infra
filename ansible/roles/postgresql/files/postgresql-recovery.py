#!/usr/bin/env python3
"""Verify and transactionally apply PostgreSQL native base backups."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile


class RecoveryError(RuntimeError):
    """A concise recovery failure that never prints database contents."""


def run_checked(argv, message):
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RecoveryError(message) from error
    if result.returncode:
        raise RecoveryError(message)
    return result.stdout


def verify_bundle(directory, expected_version):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("recovery archive is not a real directory")

    cluster = root / "cluster"
    if cluster.is_symlink() or not cluster.is_dir():
        raise RecoveryError("recovery archive is missing its PostgreSQL cluster")

    version_file = cluster / "PG_VERSION"
    manifest = cluster / "backup_manifest"
    for path in (version_file, manifest):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise RecoveryError("recovery archive is missing required PostgreSQL metadata")

    version = version_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9]+", version) or version != str(expected_version):
        raise RecoveryError("recovery point and target PostgreSQL major versions do not match")

    run_checked(
        ["pg_verifybackup", str(cluster)],
        "PostgreSQL recovery point failed native backup verification",
    )
    return cluster


def verify_live_cluster(port):
    run_checked(
        ["pg_isready", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres"],
        "PostgreSQL did not become ready after recovery",
    )
    output = run_checked(
        [
            "runuser",
            "--user",
            "postgres",
            "--",
            "psql",
            "--no-psqlrc",
            "--host=/var/run/postgresql",
            f"--port={port}",
            "--username=postgres",
            "--dbname=postgres",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--command=SELECT 1",
        ],
        "PostgreSQL local-superuser verification failed after recovery",
    )
    if output.strip() != "1":
        raise RecoveryError("PostgreSQL local-superuser verification returned an unexpected result")


def restore(directory, target_directory, service, port, expected_version):
    source_cluster = verify_bundle(directory, expected_version)
    target = Path(target_directory)
    if target.is_symlink() or not target.is_dir():
        raise RecoveryError("target PostgreSQL data directory is not a real directory")
    if not re.fullmatch(r"postgresql@[0-9]+-[A-Za-z0-9_.-]+", service):
        raise RecoveryError("target PostgreSQL service name is invalid")
    if not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise RecoveryError("target PostgreSQL port is invalid")
    if source_cluster.stat().st_dev != target.parent.stat().st_dev:
        raise RecoveryError("staged PostgreSQL data must share a filesystem with the target")

    rollback = Path(
        tempfile.mkdtemp(prefix=".homelab-postgresql-rollback-", dir=str(target.parent))
    )
    rollback.rmdir()
    original_moved = False
    replacement_installed = False

    try:
        run_checked(["systemctl", "stop", service], "could not stop PostgreSQL before recovery")
        os.replace(target, rollback)
        original_moved = True
        os.replace(source_cluster, target)
        replacement_installed = True
        run_checked(["systemctl", "start", service], "could not start the recovered PostgreSQL cluster")
        verify_live_cluster(int(port))
    except (OSError, RecoveryError) as error:
        try:
            run_checked(["systemctl", "stop", service], "could not stop PostgreSQL for rollback")
            if replacement_installed and target.exists():
                shutil.rmtree(target)
            if original_moved and rollback.exists():
                os.replace(rollback, target)
            run_checked(["systemctl", "start", service], "could not restart the original PostgreSQL cluster")
            verify_live_cluster(int(port))
        except (OSError, RecoveryError) as rollback_error:
            raise RecoveryError(
                "PostgreSQL recovery failed and rollback could not be verified; "
                f"the original cluster is retained at {rollback}"
            ) from rollback_error
        raise RecoveryError(
            "PostgreSQL recovery failed; the original cluster was restored and verified"
        ) from error

    if original_moved:
        try:
            shutil.rmtree(rollback)
        except OSError as error:
            raise RecoveryError(
                "PostgreSQL recovery succeeded, but the retained original cluster "
                f"could not be removed at {rollback}"
            ) from error
    print(f"Restored PostgreSQL {expected_version} recovery point and verified local superuser access.")


def main(argv):
    if len(argv) == 4 and argv[1] == "verify":
        verify_bundle(argv[2], argv[3])
        return 0
    if len(argv) == 7 and argv[1] == "restore":
        restore(argv[2], argv[3], argv[4], argv[5], argv[6])
        return 0
    raise RecoveryError(
        "usage: postgresql-recovery.py verify <bundle> <major> | "
        "restore <bundle> <data-dir> <service> <port> <major>"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoveryError as error:
        print(f"PostgreSQL recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
