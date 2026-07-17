"""Portable consumer-to-toolkit feedback bundles.

`feedback export` projects one consumer matter specimen plus its cited quanta
into a single portable JSON document and mutates nothing outside the declared
output path. Import stays a human action: a person moves the bundle into the
toolkit repository and feeds it to Intake as an ordinary dump. `feedback
validate` checks a bundle against its published schema alone, independent of
any toolkit-repository state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import EXIT_MALFORMED, IsotopeError
from .paths import Project
from .revisions import revision, write_canonical
from .schemas import validate as validate_schema
from .schemas import validate_quantum_payload


def _compact_invocation(record: dict[str, Any]) -> dict[str, Any]:
    """The bundle carries only the compact outcome — never capabilities,
    questions, source revisions, or worker trail."""
    result = record["result"]
    if result is not None:
        validate_schema("compact-result", result)
        entity = result["entity"]
        compact_entity = None
        if entity is not None:
            if not isinstance(entity.get("kind"), str) or not entity["kind"]:
                raise IsotopeError(
                    "schema-invalid",
                    "A compact invocation entity requires its kind.",
                    EXIT_MALFORMED,
                    {"path": "/result/entity/kind"},
                )
            compact_entity = {"kind": entity["kind"]}
            for field in ("id", "revision"):
                if field in entity:
                    compact_entity[field] = entity[field]
        result = {
            "status": result["status"],
            "outcome": result["outcome"],
            "entity": compact_entity,
        }
    return {
        "id": record["id"],
        "reaction": record["reaction"],
        "status": record["status"],
        "result": result,
    }


def validate_bundle(value: Any) -> str:
    validate_schema("feedback-bundle", value)
    for index, item in enumerate(value["evidence"]):
        quantum = item["quantum"]
        try:
            validate_schema("quantum", quantum)
            validate_quantum_payload(quantum["type"], quantum["payload"])
        except IsotopeError as exc:
            raise IsotopeError(
                "schema-invalid",
                "A bundle evidence item is not a valid quantum.",
                EXIT_MALFORMED,
                {"path": f"/evidence/{index}/quantum", "reason": exc.message},
            ) from exc
    return revision(value)


def export(project: Project, slug: str, payload: Any, output: str) -> dict[str, Any]:
    from . import specimens
    from .invocations import read_invocation
    from .quanta import read_quantum
    from .setup import load_synthesis

    if not isinstance(payload, dict) or set(payload) != {"reaction", "evidence"}:
        raise IsotopeError(
            "invalid-input",
            "A feedback export requires exactly two fields: 'reaction' and 'evidence'.",
            EXIT_MALFORMED,
            {"path": "/"},
        )
    evidence = payload["evidence"]
    if not isinstance(evidence, list) or not evidence or len(set(evidence)) != len(evidence):
        raise IsotopeError(
            "invalid-input",
            "Evidence cites at least one quantum, each at most once.",
            EXIT_MALFORMED,
            {"path": "/evidence"},
        )
    synthesis, _ = load_synthesis(project)
    located = specimens.locate(project, slug)
    value, specimen_revision = specimens.read_validated(located)
    items = []
    for quantum_id in evidence:
        quantum = read_quantum(project, quantum_id)
        cited_invocation = quantum["provenance"].get("invocation")
        invocations = []
        if cited_invocation is not None:
            invocations.append(_compact_invocation(read_invocation(project, cited_invocation)))
        items.append({"quantum": quantum, "invocations": invocations})
    bundle = {
        "schema_version": "1",
        "kind": "isotope-feedback",
        "package_version": synthesis["source_version"],
        "reaction": payload["reaction"],
        "matter": value["matter"]["content"],
        "source": {"slug": slug, "revision": specimen_revision},
        "evidence": items,
    }
    bundle_revision = validate_bundle(bundle)
    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    write_canonical(destination, bundle)
    return {"path": output, "revision": bundle_revision, "evidence_count": len(items)}
