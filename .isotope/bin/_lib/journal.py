"""Typed transaction journal and idempotent recovery dispatcher.

One journaled transaction is an ordered list of atomic file writes against
Isotope-managed lifecycle state. The journal is written only after every
validation has passed, immediately before the first write, and is removed as
the final step. Recovery inspects the durable revisions and drives the
repository to exactly one correct outcome; the crash/retry contract lives in
docs/isotope/RECOVERY.md.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .errors import EXIT_CONFLICT, EXIT_INTERNAL, IsotopeError
from .locks import project_lock
from .paths import ISOTOPE_DIR, JOURNAL_FILE, Project, is_journal_destination, is_managed_path
from .revisions import bytes_revision, load_json, revision, write_canonical
from .schemas import validate as validate_schema


@dataclass(frozen=True)
class JournalWrite:
    """One atomic replace (value), create (prior None), or delete (new None)."""

    path: str  # repo-relative POSIX path under Isotope-managed state
    prior_revision: str | None
    new_revision: str | None
    value: Any = None
    format: str = "json"

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "path": self.path,
            "prior_revision": self.prior_revision,
            "new_revision": self.new_revision,
        }
        if self.new_revision is not None:
            record["value"] = self.value
        if self.format != "json":
            record["format"] = self.format
        return record


def journal_path(project: Project) -> Path:
    return _managed_target(project, f"{ISOTOPE_DIR}/{JOURNAL_FILE}")


def _refused(message: str, details: dict[str, Any]) -> IsotopeError:
    return IsotopeError("recovery-refused", message, EXIT_CONFLICT, details)


def _value_revision(write: JournalWrite) -> str:
    if write.format == "text":
        if not isinstance(write.value, str):
            raise IsotopeError("internal", "A text journal write requires a string value.", EXIT_INTERNAL)
        return bytes_revision(write.value.encode("utf-8"))
    if write.format != "json":
        raise IsotopeError("internal", "Unknown journal write format.", EXIT_INTERNAL)
    return revision(write.value)


def _check_writes(journal_type: str, writes: list[JournalWrite]) -> None:
    if not writes:
        raise IsotopeError(
            "internal", "A journaled transaction requires at least one write.", EXIT_INTERNAL
        )
    for write in writes:
        if not is_journal_destination(write.path, journal_type):
            raise _refused(
                "The journal only writes Isotope-managed lifecycle state.",
                {"path": write.path},
            )
        if write.new_revision is not None and _value_revision(write) != write.new_revision:
            raise IsotopeError(
                "internal",
                "A journal write's value does not hash to its declared new revision.",
                EXIT_INTERNAL,
                {"path": write.path},
            )
        if write.new_revision is None and write.prior_revision is None:
            raise IsotopeError(
                "internal",
                "A journal write must create, replace, or delete.",
                EXIT_INTERNAL,
                {"path": write.path},
            )


def _current_revision(project: Project, relative: str, journal_type: str, format: str) -> str | None:
    path = _managed_target(project, relative, journal_type)
    if not path.is_file():
        return None
    if format == "text":
        return bytes_revision(path.read_bytes())
    value, _ = load_json(path)
    return revision(value)


def _apply_write(project: Project, write: JournalWrite) -> None:
    """Apply one already-authorized write; kept as the atomic-write test seam."""
    path = _managed_target(project, write.path, "any")
    if write.new_revision is None:
        path.unlink(missing_ok=True)
    else:
        if write.format == "text":
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(write.value.encode("utf-8"))
            os.replace(temp, path)
        else:
            write_canonical(path, write.value)


def _restore(project: Project, relative: str, original: bytes | None) -> None:
    path = _managed_target(project, relative, "any")
    if original is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".restore.tmp")
    temp.write_bytes(original)
    os.replace(temp, path)


def run_transaction(
    project: Project, *, journal_type: str, operation: str, writes: list[JournalWrite]
) -> None:
    """Apply validated writes under the caller-held lock, journaled and recoverable.

    Ordinary in-process failure restores every already-applied write from the
    captured original bytes and re-raises; process death leaves the journal for
    `recover`, which rolls the repository forward or back per RECOVERY.md.
    """
    _check_writes(journal_type, writes)
    record = {
        "schema_version": "1",
        "type": journal_type,
        "operation": operation,
        "writes": [write.as_record() for write in writes],
    }
    validate_schema("journal", record)
    originals: list[bytes | None] = []
    for write in writes:
        path = _managed_target(project, write.path, journal_type)
        originals.append(path.read_bytes() if path.is_file() else None)
    write_canonical(journal_path(project), record)
    applied = 0
    try:
        for write in writes:
            _apply_write(project, write)
            applied += 1
    except Exception:
        # Ordinary write failures preserve the caller-visible transaction boundary.
        # A process death cannot execute this rollback; the journal handles that case.
        try:
            for index in range(applied, -1, -1):
                if index < len(writes):
                    _restore(project, writes[index].path, originals[index])
            finish(project)
        except Exception:
            pass  # Leave the journal for deterministic recovery on the next CLI call.
        raise
    finish(project)


def _managed_target(project: Project, relative: str, journal_type: str | None = None) -> Path:
    """Resolve one journal destination inside the declared managed path set."""
    allowed = is_managed_path(relative) if journal_type is None else is_journal_destination(relative, journal_type)
    if not allowed:
        raise _refused(
            "The journal only writes Isotope-managed lifecycle state.",
            {"path": relative},
        )
    root = project.root.resolve()
    target = project.root / relative
    resolved = target.resolve(strict=False)
    try:
        resolved_relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise _refused(
            "The journal path resolves outside the consumer repository.",
            {"path": relative, "resolved": str(resolved)},
        ) from exc
    resolved_allowed = is_managed_path(resolved_relative) if journal_type is None else is_journal_destination(resolved_relative, journal_type)
    if not resolved_allowed:
        raise _refused(
            "The journal path resolves outside Isotope-managed lifecycle state.",
            {"path": relative, "resolved": resolved_relative},
        )
    return target


def finish(project: Project) -> None:
    journal_path(project).unlink(missing_ok=True)


def _write_state(project: Project, entry: dict[str, Any], journal_type: str) -> str:
    """Classify one journal write against durable state: 'prior', 'new', or 'other'."""
    current = _current_revision(project, str(entry["path"]), journal_type, entry.get("format", "json"))
    if current == entry["new_revision"]:
        return "new"
    if current == entry["prior_revision"]:
        return "prior"
    return "other"


def _recover_locked(project: Project) -> bool:
    path = journal_path(project)
    if not path.is_file():
        return False
    record, _ = load_json(path)
    try:
        validate_schema("journal", record)
    except IsotopeError as exc:
        raise _refused(
            "The pending journal is malformed.",
            {"path": f"{ISOTOPE_DIR}/{JOURNAL_FILE}", "reason": exc.message},
        ) from exc
    entries = record["writes"]
    for entry in entries:
        if not is_journal_destination(str(entry["path"]), record["type"]):
            raise _refused(
                "The pending journal names a path outside Isotope-managed state.",
                {"path": str(entry["path"])},
            )
        candidate = JournalWrite(
            path=str(entry["path"]), prior_revision=entry["prior_revision"],
            new_revision=entry["new_revision"], value=entry.get("value"),
            format=entry.get("format", "json"),
        )
        if entry["new_revision"] is not None and (
            "value" not in entry or _value_revision(candidate) != entry["new_revision"]
        ):
            raise _refused(
                "The pending journal cannot reproduce its declared new revision.",
                {"path": str(entry["path"])},
            )
    states = [_write_state(project, entry, record["type"]) for entry in entries]
    applied = 0
    for state in states:
        if state == "new":
            applied += 1
        else:
            break
    if any(state == "other" for state in states) or any(
        state != "prior" for state in states[applied:]
    ):
        raise _refused(
            "The durable files and the pending journal do not form a recoverable state.",
            {
                "operation": record["operation"],
                "paths": [str(entry["path"]) for entry in entries],
                "states": states,
            },
        )
    if applied > 0:
        for entry in entries[applied:]:
            write = JournalWrite(
                path=str(entry["path"]),
                prior_revision=entry["prior_revision"],
                new_revision=entry["new_revision"],
                value=entry.get("value"),
                format=entry.get("format", "json"),
            )
            _apply_write(project, write)
    finish(project)
    return True


def recover(project: Project) -> bool:
    """Complete or roll back any pending journaled transaction. Idempotent."""
    if not journal_path(project).is_file():
        return False
    with project_lock(project):
        return _recover_locked(project)


@contextmanager
def transaction_scope(project: Project) -> Iterator[None]:
    """Hold the project lock across recovery, validation, and semantic mutation."""
    with project_lock(project):
        _recover_locked(project)
        yield
