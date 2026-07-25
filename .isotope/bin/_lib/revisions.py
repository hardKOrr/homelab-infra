"""Canonical JSON loading, serialization, atomic writes, and SHA-256 revisions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import EXIT_INTERNAL, EXIT_MALFORMED, IsotopeError


def canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IsotopeError(
            "malformed-json",
            "The value cannot be represented as canonical JSON.",
            EXIT_MALFORMED,
            {"reason": str(exc)},
        ) from exc
    return (text + "\n").encode("utf-8")


def revision(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def bytes_revision(value: bytes) -> str:
    """Revision for exact generated text/source bytes rather than canonical JSON."""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IsotopeError(
                "duplicate-json-key",
                f"JSON object contains duplicate key {key!r}.",
                EXIT_MALFORMED,
                {"key": key},
            )
        result[key] = value
    return result


def parse_json(text: str, origin: str) -> Any:
    """Parse JSON with duplicate keys and non-finite numbers rejected."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except IsotopeError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        details = {"source": origin, "reason": str(exc)}
        if isinstance(exc, json.JSONDecodeError):
            details.update({"line": exc.lineno, "column": exc.colno})
        raise IsotopeError(
            "malformed-json",
            "The document contains malformed JSON.",
            EXIT_MALFORMED,
            details,
        ) from exc


def load_json(path: Path) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise IsotopeError(
            "malformed-json",
            "The document is not readable UTF-8 JSON.",
            EXIT_MALFORMED,
            {"path": str(path), "reason": str(exc)},
        ) from exc
    return parse_json(text, str(path)), raw


def write_canonical(path: Path, value: Any) -> str:
    """Atomically replace `path` with the canonical serialization of `value`."""
    data = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise IsotopeError(
            "write-failed",
            "The file could not be replaced atomically.",
            EXIT_INTERNAL,
            {"path": str(path), "reason": str(exc)},
        ) from exc
    return revision(value)
