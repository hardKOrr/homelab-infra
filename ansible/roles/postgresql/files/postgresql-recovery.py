#!/usr/bin/env python3
"""Verify a staged PostgreSQL native base backup and a restored live cluster.

ansible/tasks/recovery/restore-swap.yml swaps the data directory; this helper is its
staged and verified hooks.
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys


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

    verify_binary = shutil.which("pg_verifybackup")
    if verify_binary is None:
        # Debian installs versioned PostgreSQL utilities under /usr/lib rather than
        # exposing every client tool through pg_wrapper on PATH.
        verify_binary = f"/usr/lib/postgresql/{expected_version}/bin/pg_verifybackup"

    run_checked(
        [verify_binary, str(cluster)],
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


def main(argv):
    if len(argv) == 4 and argv[1] == "verify":
        verify_bundle(argv[2], argv[3])
        return 0
    if len(argv) == 3 and argv[1] == "check-live" and argv[2].isdigit() and 1 <= int(argv[2]) <= 65535:
        verify_live_cluster(int(argv[2]))
        return 0
    raise RecoveryError(
        "usage: postgresql-recovery.py verify <bundle> <major> | check-live <port>"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoveryError as error:
        print(f"PostgreSQL recovery failed: {error}", file=sys.stderr)
        raise SystemExit(1)
