"""Durable catalyst invocations: records, questions, answers, compact status.

An invocation freezes one catalyst attempt under `.isotope/invocations/<id>.json`.
The wrapper retains the one-use completion capability; only its hash is ever
stored. Answers and status transitions are journaled, schema-valid, and
retry-safe: repeating an identical semantic answer is idempotent, and a
different answer for an answered question is a typed conflict.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, IsotopeError
from .journal import JournalWrite, run_transaction, transaction_scope
from .paths import INVOCATIONS_DIR, ISOTOPE_DIR, Project
from .revisions import canonical_bytes, load_json, revision
from .schemas import validate as validate_schema


def invocations_dir(project: Project) -> Path:
    return project.root / ISOTOPE_DIR / INVOCATIONS_DIR


def invocation_path(project: Project, invocation_id: str) -> Path:
    return invocations_dir(project) / f"{invocation_id}.json"


def invocation_relative(invocation_id: str) -> str:
    return f"{ISOTOPE_DIR}/{INVOCATIONS_DIR}/{invocation_id}.json"


def capability_hash(capability: str) -> str:
    """Hash of the wrapper-held one-use completion capability; never store the input."""
    return "sha256:" + hashlib.sha256(capability.encode("utf-8")).hexdigest()


def capability_matches(capability: str, expected_hash: str) -> bool:
    """Constant-time comparison for a wrapper-retained completion capability."""
    return hmac.compare_digest(capability_hash(capability), expected_hash)


def read_invocation(project: Project, invocation_id: str) -> dict[str, Any]:
    path = invocation_path(project, invocation_id)
    if not path.is_file():
        raise IsotopeError(
            "invocation-not-found",
            f"No invocation {invocation_id!r} exists.",
            EXIT_NOT_FOUND,
            {"invocation": invocation_id},
        )
    value, _ = load_json(path)
    validate_schema("invocation", value)
    if value["id"] != invocation_id:
        raise IsotopeError(
            "schema-invalid",
            "The invocation id must match its filename.",
            EXIT_MALFORMED,
            {"path": "/id", "expected": invocation_id},
        )
    return value


def _next_id(project: Project) -> str:
    directory = invocations_dir(project)
    highest = 0
    if directory.is_dir():
        for path in directory.glob("I*.json"):
            stem = path.stem
            if stem.startswith("I") and stem[1:].isdigit():
                highest = max(highest, int(stem[1:]))
    return f"I{highest + 1}"


def create_invocation(
    project: Project,
    *,
    reaction: str,
    protocol_version: str,
    coordinates: dict[str, Any],
    host: str,
    completion_capability_hash: str,
    model: str | None = None,
    predecessor: str | None = None,
    source_revisions: dict[str, str] | None = None,
    review_snapshot_revision: str | None = None,
    allowed_effects: list[str] | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the frozen invocation record before any catalyst launch."""
    with transaction_scope(project):
        if predecessor is not None:
            read_invocation(project, predecessor)  # a predecessor link must resolve
        record = {
            "schema_version": "1",
            "id": _next_id(project),
            "reaction": reaction,
            "protocol_version": protocol_version,
            "coordinates": coordinates,
            "host": host,
            "model": model,
            "predecessor": predecessor,
            "source_revisions": source_revisions or {},
            "review_snapshot_revision": review_snapshot_revision,
            "allowed_effects": allowed_effects or [],
            "completion_capability_hash": completion_capability_hash,
            "status": "created",
            "questions": questions or [],
            "blocking_condition": None,
            "result": None,
        }
        validate_schema("invocation", record)
        _validate_questions(record["questions"])
        run_transaction(
            project,
            journal_type="invocation",
            operation="invocation.create",
            writes=[JournalWrite(invocation_relative(record["id"]), None, revision(record), record)],
        )
        return record


def _validate_questions(questions: list[dict[str, Any]]) -> None:
    for index, question in enumerate(questions, 1):
        if question.get("id") != f"Q{index}":
            raise IsotopeError(
                "schema-invalid",
                "Questions must use stable contiguous IDs in array order.",
                EXIT_MALFORMED,
                {"path": f"/questions/{index - 1}/id", "expected": f"Q{index}"},
            )


def update_status(
    project: Project,
    invocation_id: str,
    *,
    status: str,
    questions: list[dict[str, Any]] | None = None,
    blocking_condition: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    expected_status: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Journaled durable status transition; idempotent for an identical update.

    `expected_status` guards observer-driven transitions: the update applies only
    while the locked re-read still shows one of the expected statuses, so a
    completion that lands concurrently is returned intact instead of overwritten.
    """
    with transaction_scope(project):
        record = read_invocation(project, invocation_id)
        if expected_status is not None and record["status"] not in expected_status:
            return record
        prior = revision(record)
        updated = dict(record)
        updated["status"] = status
        if questions is not None:
            updated["questions"] = questions
        if blocking_condition is not None:
            validate_schema("blocking-condition", blocking_condition)
            updated["blocking_condition"] = blocking_condition
        if result is not None:
            validate_schema("compact-result", result)
            updated["result"] = result
        validate_schema("invocation", updated)
        _validate_questions(updated["questions"])
        new = revision(updated)
        if new == prior:
            return record
        run_transaction(
            project,
            journal_type="invocation",
            operation="invocation.status",
            writes=[JournalWrite(invocation_relative(invocation_id), prior, new, updated)],
        )
        return updated


def answer(project: Project, invocation_id: str, question_id: str, payload: Any) -> dict[str, Any]:
    """Record one semantic answer; retrying the identical answer is idempotent."""
    if not isinstance(payload, dict) or set(payload) != {"answer"}:
        raise IsotopeError(
            "invalid-input",
            "The payload must be a JSON object with exactly one field: 'answer'.",
            EXIT_MALFORMED,
            {"path": "/answer"},
        )
    semantic = payload["answer"]
    if semantic is None:
        raise IsotopeError(
            "invalid-input",
            "An answer must carry a semantic value.",
            EXIT_MALFORMED,
            {"path": "/answer"},
        )
    with transaction_scope(project):
        record = read_invocation(project, invocation_id)
        question = next(
            (item for item in record["questions"] if item["id"] == question_id), None
        )
        if question is None:
            raise IsotopeError(
                "question-not-found",
                f"No question {question_id!r} exists on {invocation_id!r}.",
                EXIT_NOT_FOUND,
                {"invocation": invocation_id, "question": question_id},
            )
        existing = question.get("answer")
        if existing is not None:
            if canonical_bytes(existing) == canonical_bytes(semantic):
                return record
            raise IsotopeError(
                "answer-conflict",
                "The question already carries a different durable answer.",
                EXIT_CONFLICT,
                {"invocation": invocation_id, "question": question_id},
            )
        prior = revision(record)
        updated = {
            **record,
            "questions": [
                {**item, "answer": semantic} if item["id"] == question_id else item
                for item in record["questions"]
            ],
        }
        validate_schema("invocation", updated)
        run_transaction(
            project,
            journal_type="invocation",
            operation="invocation.answer",
            writes=[JournalWrite(invocation_relative(invocation_id), prior, revision(updated), updated)],
        )
        return updated


def status_data(project: Project, invocation_id: str) -> dict[str, Any]:
    """The compact projection Operate receives; detail stays in the record."""
    record = read_invocation(project, invocation_id)
    if record["status"] == "running":
        from .locks import invocation_lease_active

        if not invocation_lease_active(project, invocation_id):
            record = update_status(
                project,
                invocation_id,
                status="failed",
                result={"status": "failed", "outcome": None, "entity": None},
                expected_status=("running",),
            )
    return {
        "invocation_id": record["id"],
        "reaction": record["reaction"],
        "status": record["status"],
        "predecessor": record["predecessor"],
        "questions": [
            {"id": item["id"], "text": item["text"], "answered": item.get("answer") is not None}
            for item in record["questions"]
        ],
        "blocking_condition": record["blocking_condition"],
        "result": record["result"],
    }
