"""Narrow Architect and Operate projections for the long-lived owner thread."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from . import docs, invocations, operations, setup, specimens
from .errors import EXIT_MALFORMED, IsotopeError
from .manifest import load as load_manifest
from .manifest import source as manifest_source
from .paths import CULTURES_DIR, CULTURE_STAGES, INVOCATIONS_DIR, ISOTOPE_DIR, Project
from .revisions import load_json


def _reaction_configuration() -> dict[str, Any]:
    root = setup.resource_root() / "reactions"
    index, _ = load_json(root / "index.json")
    if not isinstance(index, dict):
        raise IsotopeError("distribution-invalid", "The reaction index must be an object.", EXIT_MALFORMED)
    protocols = []
    for reaction, relative in sorted(index.items()):
        if not isinstance(reaction, str) or not isinstance(relative, str):
            raise IsotopeError("distribution-invalid", "The reaction index contains a malformed entry.", EXIT_MALFORMED)
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.name != "protocol.json":
            raise IsotopeError(
                "distribution-invalid",
                "A reaction protocol path escapes its package root.",
                EXIT_MALFORMED,
                {"reaction": reaction, "path": relative},
            )
        protocol_path = root.joinpath(*candidate.parts)
        protocol, _ = load_json(protocol_path)
        required = ("id", "version", "brief_map", "readout_schema", "authority", "assets")
        if not isinstance(protocol, dict) or protocol.get("id") != reaction or any(name not in protocol for name in required):
            raise IsotopeError(
                "distribution-invalid",
                "A reaction protocol does not match its index declaration.",
                EXIT_MALFORMED,
                {"reaction": reaction, "path": relative},
            )
        for resource_name in (protocol["brief_map"], protocol["readout_schema"]):
            resource = protocol_path.parent / resource_name
            if not resource.is_file():
                raise IsotopeError(
                    "distribution-invalid",
                    "A reaction protocol resource is missing.",
                    EXIT_MALFORMED,
                    {"reaction": reaction, "resource": resource_name},
                )
        protocols.append({
            "reaction": reaction,
            "version": protocol["version"],
            "catalyst_authority": protocol["authority"].get("catalyst"),
        })
    return {"state": "ready", "count": len(protocols), "protocols": protocols}


def architect_inspect(project: Project) -> dict[str, Any]:
    """Inspect durable consumer shape without returning document or protocol bodies."""
    manifest, manifest_revision = load_manifest(project)
    atlas, atlas_source = docs.validate_docs(project)
    registry, registry_revision = setup.load_registry(project)
    synthesis = setup.inspect(project)
    hosts = []
    for host, entry in sorted(registry["hosts"].items()):
        hosts.append({
            "host": host,
            "enabled": entry["enabled"],
            "available": entry["available"],
            "default_model": entry["default_model"],
            "models": [
                {"id": model["id"], "enabled": model["enabled"], "reactions": model["reactions"]}
                for model in entry["models"]
            ],
        })
    registry_ready = any(
        host["enabled"] and any(model["enabled"] for model in host["models"])
        for host in hosts
    )
    return {
        "state": "ready" if synthesis["state"] == "ready" and registry_ready else "attention",
        "manifest": {
            "source": manifest_source(manifest_revision),
            "autonomy": manifest.get("autonomy", "declared"),
            "gates": sorted(manifest.get("gates", {})),
            "atlas_concepts": [entry["concept"] for entry in manifest.get("docs", [])],
            "tools_configured": bool(manifest.get("tools")),
        },
        "atlas": {**atlas, "source": atlas_source},
        "registry": {
            "state": "ready" if registry_ready else "empty",
            "revision": registry_revision,
            "hosts": hosts,
        },
        "reactions": _reaction_configuration(),
        "synthesis": synthesis,
    }


def _latest_round(value: dict[str, Any], change: int) -> dict[str, Any] | None:
    rounds = [item for item in value["rounds"] if item["change"] == change]
    return max(rounds, key=lambda item: item["number"], default=None)


def _assay_for(value: dict[str, Any], change: int, round_number: int) -> dict[str, Any] | None:
    return next(
        (item for item in value["assays"] if item["change"] == change and item["round"] == round_number),
        None,
    )


def _next_step(value: dict[str, Any], stage: str, *, operation_state: str | None) -> dict[str, Any]:
    if operation_state in ("landing", "landed"):
        return {"state": "ready", "kind": "operation", "id": "cleanup", "reason": operation_state}
    if stage == "stable":
        if operation_state == "armed":
            return {"state": "ready", "kind": "operation", "id": "deploy", "reason": "stabilized"}
        return {"state": "complete", "kind": None, "id": None, "reason": None}
    if stage == "matter":
        return {"state": "ready", "kind": "reaction", "id": "analyze", "reason": None}
    if operation_state == "parked":
        return {"state": "waiting", "kind": "operation", "id": "resume", "reason": "parked"}
    if operation_state != "armed":
        return {"state": "waiting", "kind": "operation", "id": "arm", "reason": "not-armed"}
    design_fields = (value.get("context"), value.get("acceptance_criteria"), value.get("changes"), value.get("verification"))
    if not all(design_fields):
        return {"state": "ready", "kind": "reaction", "id": "design", "reason": None}
    for change in value["changes"]:
        latest = _latest_round(value, change["number"])
        if latest is None or latest["status"] != "complete":
            return {"state": "ready", "kind": "reaction", "id": "construction", "reason": f"change-{change['number']}"}
        assay = _assay_for(value, change["number"], latest["number"])
        if assay is None:
            return {"state": "ready", "kind": "reaction", "id": "review", "reason": f"change-{change['number']}-round-{latest['number']}"}
        if assay["outcome"] == "CHANGES":
            return {"state": "ready", "kind": "reaction", "id": "construction", "reason": f"change-{change['number']}-changes"}
    acceptance = value["acceptances"][-1] if value["acceptances"] else None
    if acceptance is None:
        return {"state": "ready", "kind": "reaction", "id": "acceptance", "reason": None}
    if acceptance["verdict"] == "CHANGES":
        return {"state": "ready", "kind": "reaction", "id": "construction", "reason": "acceptance-changes"}
    if value.get("outcome") is None:
        return {"state": "ready", "kind": "operation", "id": "record-outcome", "reason": None}
    if value["outcome"].get("expression") is None:
        return {"state": "ready", "kind": "reaction", "id": "expression", "reason": None}
    return {"state": "ready", "kind": "operation", "id": "deploy", "reason": None}


def _specimen_summaries(project: Project, operating: dict[str, Any] | None) -> list[dict[str, Any]]:
    culture_root = project.root / ISOTOPE_DIR / CULTURES_DIR
    slugs = {
        path.stem
        for stage in CULTURE_STAGES
        for path in (culture_root / stage).glob("*.json")
        if path.is_file()
    }
    summaries = []
    for slug in sorted(slugs):
        located = specimens.locate(project, slug)
        value, specimen_revision = specimens.read_validated(located)
        operation_state = operating["state"] if operating is not None and operating["slug"] == slug else None
        armed = operation_state == "armed"
        summaries.append({
            "slug": slug,
            "stage": located.stage,
            "goal": value["goal"],
            "depends_on": list(value["depends_on"]),
            "revision": specimen_revision,
            "armed": armed,
            "readiness": _next_step(value, located.stage, operation_state=operation_state),
        })
    stage_order = {"flux": 0, "matter": 1, "stable": 2}
    return sorted(summaries, key=lambda item: (not item["armed"], stage_order[item["stage"]], item["slug"]))


def _invocation_projection(project: Project, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    directory = project.root / ISOTOPE_DIR / INVOCATIONS_DIR
    ids = sorted(
        (path.stem for path in directory.glob("I*.json") if path.stem[1:].isdigit()),
        key=lambda value: int(value[1:]),
    ) if directory.is_dir() else []
    questions = []
    highlights = []
    for invocation_id in ids:
        record = invocations.read_invocation(project, invocation_id)
        slug = record.get("coordinates", {}).get("slug")
        unanswered = [
            {"id": item["id"], "text": item["text"]}
            for item in record["questions"]
            if item.get("answer") is None
        ]
        if record["status"] == "needs-user" and unanswered:
            questions.append({
                "invocation_id": invocation_id,
                "reaction": record["reaction"],
                "slug": slug,
                "questions": unanswered,
            })
        if record["result"] is not None:
            highlights.append({
                "invocation_id": invocation_id,
                "reaction": record["reaction"],
                "slug": slug,
                "result": record["result"],
            })
    return questions[:limit], highlights[-limit:], len(questions), len(highlights)


def operate_status(project: Project, *, limit: int) -> dict[str, Any]:
    """Return bounded long-range coordination state without reaction-private payloads."""
    operating = operations.status(project)
    operating_record = operating["operating"]
    summaries = _specimen_summaries(project, operating_record)
    questions, highlights, question_total, highlight_total = _invocation_projection(project, limit)
    return {
        "operating": operating,
        "specimens": summaries[:limit],
        "specimen_total": len(summaries),
        "questions": questions,
        "question_total": question_total,
        "highlights": highlights,
        "highlight_total": highlight_total,
        "limit": limit,
        "truncated": any(total > limit for total in (len(summaries), question_total, highlight_total)),
    }
