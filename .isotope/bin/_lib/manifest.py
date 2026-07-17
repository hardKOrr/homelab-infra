"""Consumer manifest loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import EXIT_MALFORMED, EXIT_NOT_FOUND, IsotopeError
from .paths import ISOTOPE_DIR, MANIFEST_FILE, Project
from .revisions import parse_json, revision
from .schemas import validate


def path(project: Project) -> Path:
    return project.root / ISOTOPE_DIR / MANIFEST_FILE


def validate_value(value: Any) -> None:
    """Validate the complete consumer-manifest contract."""
    validate("manifest", value)
    gates = value.get("gates", {})
    for gate_id, command in gates.items():
        if not gate_id.strip():
            raise IsotopeError(
                "manifest-invalid",
                "Manifest gate IDs must be non-empty strings.",
                EXIT_MALFORMED,
                {"path": "/gates"},
            )
        if not isinstance(command, str) or not command.strip():
            raise IsotopeError(
                "manifest-invalid",
                "Manifest gate commands must be non-empty strings.",
                EXIT_MALFORMED,
                {"path": f"/gates/{gate_id}"},
            )


def load(project: Project) -> tuple[dict[str, Any], str]:
    manifest_path = path(project)
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IsotopeError(
            "manifest-not-found",
            "The Isotope manifest does not exist.",
            EXIT_NOT_FOUND,
            {"path": f"{ISOTOPE_DIR}/{MANIFEST_FILE}"},
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise IsotopeError(
            "manifest-malformed",
            "The Isotope manifest is not readable UTF-8 JSON.",
            EXIT_MALFORMED,
            {"path": f"{ISOTOPE_DIR}/{MANIFEST_FILE}", "reason": str(exc)},
        ) from exc
    value = parse_json(text, f"{ISOTOPE_DIR}/{MANIFEST_FILE}")
    validate_value(value)
    return value, revision(value)


def source(manifest_revision: str) -> dict[str, str]:
    return {
        "path": f"{ISOTOPE_DIR}/{MANIFEST_FILE}",
        "revision": manifest_revision,
    }
