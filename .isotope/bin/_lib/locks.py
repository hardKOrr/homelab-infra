"""Cross-platform advisory serialization for Isotope repository mutations."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .paths import Project


@contextmanager
def project_lock(project: Project):
    """Serialize compare-and-swap and recovery within one consumer repository."""
    path = project.git_common_dir / "isotope-transaction.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_handle(path: Path) -> BinaryIO:
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    return handle


def acquire_invocation_lease(project: Project, invocation_id: str) -> BinaryIO:
    """Hold one wrapper lease until its external result is terminal."""
    handle = _lock_handle(project.git_common_dir / f"isotope-invocation-{invocation_id}.lock")
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_invocation_lease(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    _remove_lease_file(Path(handle.name))


def _remove_lease_file(path: Path) -> None:
    # A concurrent observer can hold the file open on Windows; the lease state
    # is already released, so a leftover file is reclaimed by the next probe.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def invocation_lease_active(project: Project, invocation_id: str) -> bool:
    """Return whether another process still owns the invocation wrapper lease."""
    path = project.git_common_dir / f"isotope-invocation-{invocation_id}.lock"
    handle = _lock_handle(path)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True
    finally:
        handle.close()
    _remove_lease_file(path)
    return False
