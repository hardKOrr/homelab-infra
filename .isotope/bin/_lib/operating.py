"""Armed operating state consumed by specimen transactions.

`.isotope/operating.json` exists only while one flux specimen is armed. It
binds the armed slug to its branch, resolved base commit, specimen revision
tail, and recovery state. The arm/status/teardown state machines live in
`operations.py`; this module owns the primitives transactions need.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_REFUSED, IsotopeError
from .paths import ISOTOPE_DIR, OPERATING_FILE, Project
from .revisions import load_json
from .schemas import validate as validate_schema


OPERATING_RELATIVE = f"{ISOTOPE_DIR}/{OPERATING_FILE}"


def operating_path(project: Project) -> Path:
    return project.root / ISOTOPE_DIR / OPERATING_FILE


def read_operating(project: Project) -> dict[str, Any] | None:
    path = operating_path(project)
    if not path.is_file():
        return None
    value, _ = load_json(path)
    try:
        validate_schema("operating", value)
        state = value["state"]
        parked = {"parked_head"}
        landing = {"target_branch", "operation_head", "landed_commit", "landing_reason"}
        present = {name for name in parked | landing if name in value}
        expected = parked if state == "parked" else landing if state in ("landing", "landed") else set()
        if present != expected:
            raise IsotopeError(
                "schema-invalid",
                "Operating recovery fields must exactly match the lifecycle state.",
                EXIT_MALFORMED,
                {"state": state, "expected": sorted(expected), "actual": sorted(present)},
            )
    except IsotopeError as exc:
        raise IsotopeError(
            "operating-malformed",
            "operating.json does not describe an armed operation.",
            EXIT_MALFORMED,
            {"path": OPERATING_RELATIVE, "reason": exc.message},
        ) from exc
    return value


def require_armed(project: Project, slug: str, current_revision: str) -> dict[str, Any]:
    """The one specimen transactions may drive is the flux specimen named by operating.json."""
    operating = read_operating(project)
    if operating is None:
        raise IsotopeError(
            "no-armed-operation",
            "This transaction requires an armed operation.",
            EXIT_REFUSED,
            {"slug": slug},
        )
    if operating["slug"] != slug:
        raise IsotopeError(
            "operation-mismatch",
            "The armed operation drives a different specimen.",
            EXIT_REFUSED,
            {"slug": slug, "armed": operating["slug"]},
        )
    if operating["state"] != "armed":
        raise IsotopeError(
            "operation-not-armed",
            "Reaction writes require the operation to be in armed state.",
            EXIT_REFUSED,
            {"slug": slug, "state": operating["state"], "next_action": "resume the operation"},
        )
    if operating["specimen_revision"] != current_revision:
        raise IsotopeError(
            "chain-broken",
            "The armed operation's recorded revision does not match the specimen; "
            "an out-of-band edit is likely.",
            EXIT_CONFLICT,
            {"recorded": operating["specimen_revision"], "actual": current_revision},
        )
    return operating


def advanced_tail(operating: dict[str, Any], new_revision: str) -> dict[str, Any]:
    updated = dict(operating)
    updated["specimen_revision"] = new_revision
    return updated
