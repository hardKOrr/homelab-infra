"""Native Design protocol mapping, briefing, and durable whole-design recording."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from . import docs as isotope_docs
from . import gitops, invocations, manifest, specimens
from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .operating import read_operating
from .revisions import bytes_revision, load_json, revision
from .setup import load_registry, load_synthesis, require_ready, resource_root


REACTION = "design"


def _reaction_root() -> Path:
    return resource_root() / "reactions" / REACTION


def protocol() -> dict[str, Any]:
    index, _ = load_json(resource_root() / "reactions" / "index.json")
    relative = index.get(REACTION) if isinstance(index, dict) else None
    if relative != "design/protocol.json":
        raise IsotopeError("protocol-invalid", "The Design protocol is absent from the reaction catalog.", EXIT_MALFORMED)
    value, _ = load_json(resource_root() / "reactions" / relative)
    if value.get("id") != REACTION or value.get("version") != "1" or value.get("transport") != "native-only":
        raise IsotopeError("protocol-invalid", "The Design protocol root is invalid.", EXIT_MALFORMED)
    return value


def brief_map() -> dict[str, Any]:
    value, _ = load_json(_reaction_root() / "brief.map.json")
    return value


def readout_schema() -> dict[str, Any]:
    value, _ = load_json(_reaction_root() / "readout.schema.json")
    return value


def options(project) -> dict[str, Any]:
    registry, registry_revision = load_registry(project)
    choices = []
    for host, entry in sorted(registry["hosts"].items()):
        if not entry["enabled"]:
            continue
        for model in entry["models"]:
            if not model["enabled"] or REACTION not in model["reactions"]:
                continue
            choices.append({
                "host": host,
                "model": model["id"],
                "default": entry["default_model"] == model["id"],
                "available": entry["available"],
                "cost": model.get("cost"),
                "rate": model.get("rate"),
                "reasoning": model.get("reasoning"),
            })
    return {"reaction": REACTION, "registry_revision": registry_revision, "choices": choices}


def _select_option(project, host: str, model: str | None) -> str:
    choices = [item for item in options(project)["choices"] if item["host"] == host and item["available"] is not False]
    if model is not None:
        choices = [item for item in choices if item["model"] == model]
    else:
        defaults = [item for item in choices if item["default"]]
        if len(defaults) == 1:
            choices = defaults
    if len(choices) != 1:
        raise IsotopeError(
            "registry-option-missing",
            "The requested host/model does not resolve to one enabled Design option.",
            EXIT_REFUSED,
            {"host": host, "model": model, "candidates": [{"host": item["host"], "model": item["model"]} for item in choices], "next_action": "update .isotope/registry.json or select an exact model"},
        )
    return choices[0]["model"]


def _coordinates(project, slug: str | None) -> dict[str, Any]:
    if slug is None:
        operating = read_operating(project)
        if operating is None:
            raise IsotopeError("no-armed-operation", "An unarmed Design requires an explicit slug.", EXIT_REFUSED)
        slug = operating["slug"]
    return {"slug": slug}


def _resource_revision(path: Path) -> str:
    return bytes_revision(path.read_bytes())


def _atlas(project) -> tuple[list[dict[str, Any]], dict[str, str]]:
    entries, _ = isotope_docs.map_entries(project)
    selected = []
    revisions: dict[str, str] = {}
    for entry in entries:
        section, source = isotope_docs.section(project, entry["path"], entry["section_id"])
        selected.append({"concept": entry["concept"], **section})
        revisions[f"doc:{source['path']}"] = source["revision"]
    return selected, revisions


def _source_revisions(project, specimen_revision: str, operating: dict[str, Any], doc_revisions: dict[str, str]) -> dict[str, str]:
    _, manifest_revision = manifest.load(project)
    _, registry_revision = load_registry(project)
    _, synthesis_revision = load_synthesis(project)
    root = _reaction_root()
    return {
        "specimen": specimen_revision,
        "operating": revision(operating),
        "manifest": manifest_revision,
        "registry": registry_revision,
        "synthesis": synthesis_revision,
        "protocol": _resource_revision(root / "protocol.json"),
        "brief_map": _resource_revision(root / "brief.map.json"),
        "readout_schema": _resource_revision(root / "readout.schema.json"),
        **doc_revisions,
    }


def _resolve(project, coordinates: dict[str, Any]) -> dict[str, Any]:
    located = specimens.locate(project, coordinates["slug"])
    if located.stage != "flux":
        raise IsotopeError("wrong-stage", "Design requires a flux specimen.", EXIT_REFUSED, {"stage": located.stage})
    value, specimen_revision = specimens.read_validated(located)
    operating = read_operating(project)
    if operating is None or operating["slug"] != coordinates["slug"] or operating["specimen_revision"] != specimen_revision:
        raise IsotopeError("no-armed-operation", "Design requires the exact armed specimen revision.", EXIT_REFUSED, {"slug": coordinates["slug"]})
    if gitops.current_branch(project) != operating["branch"]:
        raise IsotopeError("operating-drift", "Design requires the armed branch to be current.", EXIT_CONFLICT)
    occupied = [field for field in specimens.DESIGN_FIELDS if value.get(field)]
    if occupied:
        raise IsotopeError("design-occupied", "Design fills a vacant design; revise landed changes through their own transactions.", EXIT_REFUSED, {"slug": coordinates["slug"], "fields": occupied})
    manifest_value, _ = manifest.load(project)
    atlas_docs, doc_revisions = _atlas(project)
    return {
        "specimen": value,
        "specimen_revision": specimen_revision,
        "operating": operating,
        "gates": manifest_value.get("gates", {}),
        "atlas_docs": atlas_docs,
        "source_revisions": _source_revisions(project, specimen_revision, operating, doc_revisions),
    }


def _slot_metadata(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    counts = {
        "specimen": 1,
        "atlas_docs": len(resolved["atlas_docs"]),
        "gates": len(resolved["gates"]),
        "operating": 1,
        "answers": 0,
        "result_destination": 0,
    }
    return [{"id": slot["id"], "ready": True, "count": counts[slot["id"]], "selector": slot["selector"]} for slot in brief_map()["slots"]]


def inspect(project, slug: str | None, *, host: str | None, model: str | None, after: str | None) -> dict[str, Any]:
    try:
        coordinates = _coordinates(project, slug)
        if host is not None:
            _select_option(project, host, model)
            require_ready(project, host)
        resolved = _resolve(project, coordinates)
        if after is not None:
            predecessor = invocations.read_invocation(project, after)
            unanswered = [item["id"] for item in predecessor["questions"] if item.get("answer") is None]
            if unanswered:
                return {"state": "needs-answer", "coordinates": coordinates, "missing": unanswered, "next_action": f"answer questions on {after}"}
            if predecessor["status"] == "blocked" and predecessor["blocking_condition"] is not None:
                facts = predecessor["blocking_condition"]["observed_state"]["facts"]
                if facts.get("design_state") == revision(resolved["source_revisions"]):
                    return {"state": "blocked", "coordinates": coordinates, "condition": predecessor["blocking_condition"]["condition"], "fingerprint": predecessor["blocking_condition"]["observed_state"]["fingerprint"], "next_action": "change the named condition before continuing"}
        return {"state": "ready", "coordinates": coordinates, "slots": _slot_metadata(resolved), "source_revisions": resolved["source_revisions"]}
    except IsotopeError as exc:
        if exc.code == "usage":
            raise
        return {"state": "not-ready", "reason": exc.code, "details": exc.details, "next_action": "repair the named readiness condition and inspect again"}


def open_invocation(project, slug: str | None, *, host: str, model: str | None, after: str | None) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    result = inspect(project, slug, host=host, model=selected_model, after=after)
    if result["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Design cannot open until inspection is ready.", EXIT_REFUSED, result)
    record = invocations.create_invocation(
        project,
        reaction=REACTION,
        protocol_version=protocol()["version"],
        coordinates=result["coordinates"],
        host=host,
        model=selected_model,
        predecessor=after,
        source_revisions=result["source_revisions"],
        review_snapshot_revision=None,
        allowed_effects=protocol()["allowed_effects"],
        completion_capability_hash=invocations.capability_hash(secrets.token_urlsafe(32)),
    )
    return {"invocation_id": record["id"], "completion_capability": None, "result": None}


def _assert_sources(project, invocation: dict[str, Any]) -> dict[str, Any]:
    require_ready(project, invocation["host"])
    resolved = _resolve(project, invocation["coordinates"])
    mismatches = {key: {"frozen": value, "current": resolved["source_revisions"].get(key)} for key, value in invocation["source_revisions"].items() if resolved["source_revisions"].get(key) != value}
    if mismatches:
        raise IsotopeError("stale-design-source", "A frozen Design source changed after open.", EXIT_CONFLICT, {"sources": mismatches})
    return resolved


def brief(project, invocation_id: str) -> dict[str, Any]:
    record = invocations.read_invocation(project, invocation_id)
    if record["reaction"] != REACTION or record["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind this Design protocol.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-briefable", "Only an open Design invocation can pull a brief.", EXIT_CONFLICT, {"status": record["status"]})
    resolved = _assert_sources(project, record)
    answers = []
    if record["predecessor"] is not None:
        predecessor = invocations.read_invocation(project, record["predecessor"])
        answers = [{"question_id": item["id"], "answer": item["answer"]} for item in predecessor["questions"] if item.get("answer") is not None]
    return {
        "schema_version": "1",
        "reaction": REACTION,
        "protocol_version": record["protocol_version"],
        "invocation_id": record["id"],
        "coordinates": record["coordinates"],
        "source_revisions": record["source_revisions"],
        "values": {
            "specimen": resolved["specimen"],
            "atlas_docs": resolved["atlas_docs"],
            "gates": resolved["gates"],
            "operating": {key: resolved["operating"][key] for key in ("slug", "branch", "base_commit", "specimen_revision")},
            "answers": answers,
            "result_destination": "/context,/acceptance_criteria,/changes,/verification",
        },
        "record_command": f"python .isotope/bin/isotope.py agent record design --invocation {record['id']} --input <json-file>",
    }


def _validate_readout(value: Any) -> dict[str, Any]:
    from .schemas import _validate

    _validate(readout_schema(), value)
    status = value["status"]
    if status == "complete":
        if value["design"] is None or value["questions"] or value["condition"] is not None or value["next_action"] is not None:
            raise IsotopeError("result-malformed", "A complete Design readout carries the design payload only.", EXIT_MALFORMED)
    elif status == "needs-user":
        if not value["questions"] or value["design"] is not None or value["next_action"] is None:
            raise IsotopeError("result-malformed", "needs-user requires questions and a next action.", EXIT_MALFORMED)
    elif status == "blocked":
        if value["condition"] is None or value["next_action"] is None or value["design"] is not None:
            raise IsotopeError("result-malformed", "blocked requires condition, facts, and next_action.", EXIT_MALFORMED)
    elif value["next_action"] is None or value["design"] is not None:
        raise IsotopeError("result-malformed", f"{status} requires a next action and no design payload.", EXIT_MALFORMED)
    return value


def _design_entity(design: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted({step.get("gate_id") for step in design["verification"] if step.get("gate_id") is not None} - set(gates))
    if unknown:
        raise IsotopeError("gate-not-found", "Design verification cites gates the manifest does not declare.", EXIT_REFUSED, {"gate_ids": unknown})
    return {
        "context": design["context"],
        "acceptance_criteria": [{"id": f"AC{index}", "criterion": criterion} for index, criterion in enumerate(design["acceptance_criteria"], 1)],
        "changes": [{"schema_version": "2", "number": index, **item} for index, item in enumerate(design["changes"], 1)],
        "verification": [{"id": f"V{index}", **item} for index, item in enumerate(design["verification"], 1)],
    }


def record(project, invocation_id: str, readout: Any) -> dict[str, Any]:
    record_value = invocations.read_invocation(project, invocation_id)
    if record_value["reaction"] != REACTION or record_value["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind Design.", EXIT_CONFLICT)
    if os.environ.get("ISOTOPE_HOST") != record_value["host"]:
        raise IsotopeError("authority-unavailable", "The active native host does not match the Design invocation.", EXIT_REFUSED, {"host": record_value["host"]})
    value = _validate_readout(readout)
    if record_value["status"] in ("needs-user", "blocked", "refused", "failed") and record_value["result"] is not None:
        return {"invocation_id": invocation_id, "result": record_value["result"]}
    if record_value["status"] == "complete" and record_value["result"] is not None and value["status"] != "complete":
        raise IsotopeError("design-race", "The invocation already recorded a different Design result.", EXIT_CONFLICT, {"invocation": invocation_id})
    status = value["status"]
    if status != "complete":
        _assert_sources(project, record_value)
    if status == "needs-user":
        questions = [{"id": f"Q{index}", "text": text, "answer": None} for index, text in enumerate(value["questions"], 1)]
        compact = {"status": "needs-user", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="needs-user", questions=questions, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "questions": [{"id": item["id"], "text": item["text"]} for item in questions]}
    if status == "blocked":
        facts = {**value["facts"], "design_state": revision(record_value["source_revisions"])}
        condition = {"condition": value["condition"], "observed_state": {"facts": facts, "fingerprint": revision(facts)}}
        compact = {"status": "blocked", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="blocked", blocking_condition=condition, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "condition": value["condition"], "next_action": value["next_action"]}
    if status in ("refused", "failed"):
        compact = {"status": status, "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status=status, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "next_action": value["next_action"]}
    manifest_value, _ = manifest.load(project)
    entity = _design_entity(value["design"], manifest_value.get("gates", {}))
    compact = specimens.record_design(
        project,
        invocation_id,
        entity=entity,
        source_guard=lambda: _assert_sources(project, record_value),
    )
    return {"invocation_id": invocation_id, "result": compact}


def map_data(map_format: str) -> dict[str, Any]:
    proto = protocol()
    mapping = brief_map()
    counts: dict[str, int] = {}
    for slot in mapping["slots"]:
        counts[slot["source"]] = counts.get(slot["source"], 0) + 1
    missing = [relative for relative in (proto["brief_map"], proto["readout_schema"], proto["entity_schema"]) if not (_reaction_root() / relative).resolve().is_file()]
    edges = [{"from": slot["source"], "to": f"brief.{slot['id']}", "selector": slot["selector"], "authority": proto["authority"]["catalyst"]} for slot in mapping["slots"]]
    edges.append({"from": "readout", "to": proto["result_destination"], "selector": "invocation-bound semantic record", "authority": proto["authority"]["broker"]})
    if map_format == "mermaid":
        lines = ["flowchart LR"]
        for index, edge in enumerate(edges, 1):
            lines.append(f"  S{index}[\"{edge['from']}\"] --> D{index}[\"{edge['to']}\"]")
        return {"reaction": REACTION, "format": "mermaid", "map": "\n".join(lines), "overlap": counts, "missing_consumers": missing}
    return {"reaction": REACTION, "format": "json", "edges": edges, "overlap": counts, "missing_consumers": missing, "unused_sources": []}
