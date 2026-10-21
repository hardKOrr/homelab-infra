#!/usr/bin/env python3
"""Read-only verification of a PBS chunk store before legacy config registration."""

import grp
import os
import pwd
import stat
import sys


CHUNK_DIRECTORY_COUNT = 1 << 16


def _check_path(path, kind, owner, owner_mode):
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError("required datastore entry is missing or unreadable") from exc

    correct_kind = (
        stat.S_ISDIR(metadata.st_mode)
        if kind == "directory"
        else stat.S_ISREG(metadata.st_mode)
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not correct_kind
        or (metadata.st_mode & 0o700) != owner_mode
        or (metadata.st_uid, metadata.st_gid) != owner
    ):
        raise ValueError("datastore entry has unexpected type, permissions or ownership")


def verify_datastore(path, *, chunk_count=CHUNK_DIRECTORY_COUNT, owner=None):
    """Check the layout PBS's native reuse path verifies, without opening it."""
    if not os.path.isabs(path) or path == "/" or not 0 < chunk_count <= CHUNK_DIRECTORY_COUNT:
        raise ValueError("invalid datastore path or chunk directory count")

    if owner is None:
        backup_user = pwd.getpwnam("backup")
        owner = (backup_user.pw_uid, grp.getgrnam("backup").gr_gid)

    chunk_root = os.path.join(path, ".chunks")
    _check_path(path, "directory", owner, 0o700)
    _check_path(chunk_root, "directory", owner, 0o700)
    _check_path(os.path.join(path, ".lock"), "file", owner, 0o600)

    expected = {f"{index:04x}" for index in range(chunk_count)}
    try:
        with os.scandir(chunk_root) as iterator:
            entries = {entry.name: entry for entry in iterator}
    except OSError as exc:
        raise ValueError("chunk directory is unreadable") from exc

    if entries.keys() != expected:
        raise ValueError("chunk directory layout is incomplete or ambiguous")

    for entry in entries.values():
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise ValueError("chunk directory entry is not a directory")
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError("chunk directory entry is unreadable") from exc
        if (metadata.st_mode & 0o700) != 0o700 or (metadata.st_uid, metadata.st_gid) != owner:
            raise ValueError("chunk directory entry has unexpected permissions or ownership")


def main(argv):
    if len(argv) != 2:
        print("usage: verify-reused-datastore.py <datastore-path>", file=sys.stderr)
        return 2
    try:
        verify_datastore(argv[1])
    except (OSError, ValueError, KeyError):
        print("retained PBS datastore structure is not safe for reuse", file=sys.stderr)
        return 1
    print("retained PBS datastore structure verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
