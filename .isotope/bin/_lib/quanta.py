"""Quanta: discrete durable evidence records under `.isotope/quanta/`.

A quantum records one observed fact that is absent from every existing
authority — specimen audit events, round gate evidence, and invocation
records — and cites those authorities by identity instead of copying them.
The four types mirror the retrospective loop: `command` (a deterministic
command execution outside gate evidence), `friction` (prompt/authority/
environment friction during a reaction), `dialect` (repository dialect worth
durable documentation), and `gap` (an Isotope product gap). The crash/retry
contract lives in docs/isotope/RECOVERY.md.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, IsotopeError
from .journal import JournalWrite, run_transaction, transaction_scope
from .paths import EVIDENCE_DIR, ISOTOPE_DIR, Project
from .revisions import canonical_bytes, load_json, revision
from .schemas import validate as validate_schema
from .schemas import validate_quantum_payload


QUANTUM_TYPES = ("command", "friction", "dialect", "gap")


def normalize_command_signature(value: str) -> str:
    """Return one stable argv spelling for a deterministic command shape."""
    try:
        argv = shlex.split(value)
    except (TypeError, ValueError) as exc:
        raise IsotopeError(
            "invalid-input",
            "A command signature must be a complete command vector.",
            EXIT_MALFORMED,
            {"path": "/payload/signature", "reason": str(exc)},
        ) from exc
    if not argv:
        raise IsotopeError(
            "invalid-input",
            "A command signature must name a command.",
            EXIT_MALFORMED,
            {"path": "/payload/signature"},
        )
    return shlex.join(argv)


def quanta_dir(project: Project) -> Path:
    return project.root / ISOTOPE_DIR / EVIDENCE_DIR


def quantum_path(project: Project, quantum_id: str) -> Path:
    return quanta_dir(project) / f"{quantum_id}.json"


def quantum_relative(quantum_id: str) -> str:
    return f"{ISOTOPE_DIR}/{EVIDENCE_DIR}/{quantum_id}.json"


def read_quantum(project: Project, quantum_id: str) -> dict[str, Any]:
    path = quantum_path(project, quantum_id)
    if not path.is_file():
        raise IsotopeError(
            "quantum-not-found",
            f"No quantum {quantum_id!r} exists.",
            EXIT_NOT_FOUND,
            {"quantum": quantum_id},
        )
    value, _ = load_json(path)
    validate_schema("quantum", value)
    validate_quantum_payload(value["type"], value["payload"])
    if value["id"] != quantum_id:
        raise IsotopeError(
            "schema-invalid",
            "The quantum id must match its filename.",
            EXIT_MALFORMED,
            {"path": "/id", "expected": quantum_id},
        )
    return value


def _quantum_ids(project: Project) -> list[str]:
    directory = quanta_dir(project)
    numbers = []
    if directory.is_dir():
        for path in directory.glob("Q*.json"):
            stem = path.stem
            if stem.startswith("Q") and stem[1:].isdigit():
                numbers.append(int(stem[1:]))
    return [f"Q{number}" for number in sorted(numbers)]


def read_all(project: Project) -> list[dict[str, Any]]:
    return [read_quantum(project, quantum_id) for quantum_id in _quantum_ids(project)]


def _next_id(project: Project) -> str:
    ids = _quantum_ids(project)
    highest = max((int(item[1:]) for item in ids), default=0)
    return f"Q{highest + 1}"


def _check_input_shape(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"type", "payload", "provenance"}:
        raise IsotopeError(
            "invalid-input",
            "A quantum requires exactly three fields: 'type', 'payload', and 'provenance'.",
            EXIT_MALFORMED,
            {"path": "/"},
        )


def _resolve_provenance(project: Project, provenance: dict[str, Any]) -> None:
    """Every cited authority must exist; citation is by identity, never by copy."""
    from . import specimens
    from .invocations import read_invocation

    if provenance.get("invocation") is None and provenance.get("slug") is None:
        raise IsotopeError(
            "invalid-input",
            "A quantum's provenance must cite an invocation or a specimen.",
            EXIT_MALFORMED,
            {"path": "/provenance"},
        )
    if provenance.get("invocation") is not None:
        invocation = read_invocation(project, provenance["invocation"])
        expected = {
            "reaction": invocation["reaction"],
            "host": invocation["host"],
            "model": invocation["model"],
            "slug": invocation["coordinates"].get("slug"),
            "change": invocation["coordinates"].get("change"),
            "round": invocation["coordinates"].get("round"),
        }
        invocation_fields = {"reaction", "host", "model"}
        for field, invocation_value in expected.items():
            comparable = field in invocation_fields or invocation_value is not None
            if field in provenance and comparable and provenance[field] != invocation_value:
                raise IsotopeError(
                    "provenance-conflict",
                    "Quantum provenance must agree with its cited invocation.",
                    EXIT_CONFLICT,
                    {
                        "invocation": provenance["invocation"],
                        "field": field,
                        "expected": invocation_value,
                        "observed": provenance[field],
                    },
                )
    if provenance.get("change") is not None and provenance.get("slug") is None:
        raise IsotopeError(
            "invalid-input",
            "A cited change requires the specimen slug that owns it.",
            EXIT_MALFORMED,
            {"path": "/provenance/change"},
        )
    if provenance.get("round") is not None and provenance.get("change") is None:
        raise IsotopeError(
            "invalid-input",
            "A cited round requires its change coordinate.",
            EXIT_MALFORMED,
            {"path": "/provenance/round"},
        )
    if provenance.get("slug") is None:
        return
    located = specimens.locate(project, provenance["slug"])
    value, _ = specimens.read_validated(located)
    if provenance.get("change") is not None:
        if not any(item["number"] == provenance["change"] for item in value["changes"]):
            raise IsotopeError(
                "change-not-found",
                "The cited change does not exist on the cited specimen.",
                EXIT_NOT_FOUND,
                {"slug": provenance["slug"], "change": provenance["change"]},
            )
    if provenance.get("round") is not None:
        cited_round = _round(value, provenance["change"], provenance["round"])
        if cited_round is None:
            raise IsotopeError(
                "round-not-found",
                "The cited round does not exist on the cited specimen.",
                EXIT_NOT_FOUND,
                {"slug": provenance["slug"], "change": provenance["change"], "round": provenance["round"]},
            )


def _round(value: dict[str, Any], change: int, number: int) -> dict[str, Any] | None:
    return next(
        (item for item in value["rounds"] if item["change"] == change and item["number"] == number),
        None,
    )


def _check_not_already_authoritative(
    project: Project, quantum_type: str, payload: dict[str, Any], provenance: dict[str, Any]
) -> None:
    """A fact whose authority already exists in a cited record is refused."""
    from . import specimens
    from .invocations import read_invocation

    if quantum_type == "command" and provenance.get("round") is not None:
        located = specimens.locate(project, provenance["slug"])
        value, _ = specimens.read_validated(located)
        cited_round = _round(value, provenance["change"], provenance["round"])
        if cited_round is not None and any(
            normalize_command_signature(item["command"]) == payload["signature"]
            for item in cited_round["evidence"]
        ):
            raise IsotopeError(
                "fact-already-authoritative",
                "The cited round already records this command as gate evidence.",
                EXIT_CONFLICT,
                {"slug": provenance["slug"], "round": cited_round["id"], "signature": payload["signature"]},
            )
    if quantum_type == "friction" and provenance.get("invocation") is not None:
        invocation = read_invocation(project, provenance["invocation"])
        blocking = invocation.get("blocking_condition")
        if blocking is not None and blocking.get("condition") == payload["condition"]:
            raise IsotopeError(
                "fact-already-authoritative",
                "The cited invocation already records this condition as its blocking condition.",
                EXIT_CONFLICT,
                {"invocation": provenance["invocation"]},
            )


def record(project: Project, payload: Any) -> tuple[dict[str, Any], bool]:
    """Record one quantum; an identical fact under identical provenance is idempotent."""
    _check_input_shape(payload)
    with transaction_scope(project):
        candidate = {
            "schema_version": "1",
            "id": _next_id(project),
            "type": payload["type"],
            "payload": payload["payload"],
            "provenance": payload["provenance"],
        }
        validate_schema("quantum", candidate)
        validate_quantum_payload(candidate["type"], candidate["payload"])
        if candidate["type"] == "command":
            candidate["payload"] = dict(candidate["payload"])
            candidate["payload"]["signature"] = normalize_command_signature(
                candidate["payload"]["signature"]
            )
        _resolve_provenance(project, candidate["provenance"])
        fact = canonical_bytes(
            {"type": candidate["type"], "payload": candidate["payload"], "provenance": candidate["provenance"]}
        )
        for existing in read_all(project):
            existing_fact = canonical_bytes(
                {"type": existing["type"], "payload": existing["payload"], "provenance": existing["provenance"]}
            )
            if existing_fact == fact:
                return existing, False
        _check_not_already_authoritative(
            project, candidate["type"], candidate["payload"], candidate["provenance"]
        )
        run_transaction(
            project,
            journal_type="quanta",
            operation="quanta.record",
            writes=[JournalWrite(quantum_relative(candidate["id"]), None, revision(candidate), candidate)],
        )
        return candidate, True


def listing_revision(records: list[dict[str, Any]]) -> str:
    """One revision over the whole listing; the pagination cursor binds to it."""
    return revision([{"id": item["id"], "revision": revision(item)} for item in records])


def filter_records(
    records: list[dict[str, Any]],
    *,
    quantum_type: str | None = None,
    slug: str | None = None,
    invocation: str | None = None,
    signature: str | None = None,
) -> list[dict[str, Any]]:
    normalized_signature = normalize_command_signature(signature) if signature is not None else None

    def keep(item: dict[str, Any]) -> bool:
        if quantum_type is not None and item["type"] != quantum_type:
            return False
        if slug is not None and item["provenance"].get("slug") != slug:
            return False
        if invocation is not None and item["provenance"].get("invocation") != invocation:
            return False
        if normalized_signature is not None and item["payload"].get("signature") != normalized_signature:
            return False
        return True

    return [item for item in records if keep(item)]
