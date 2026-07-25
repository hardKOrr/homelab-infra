"""Native Construction protocol mapping, briefing, and durable round recording."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from . import gitops, invocations, manifest, specimens
from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .operating import read_operating
from .revisions import bytes_revision, load_json, revision
from .setup import load_registry, load_synthesis, require_ready, resource_root


REACTION = "construction"


def _reaction_root() -> Path:
    return resource_root() / "reactions" / REACTION


def protocol() -> dict[str, Any]:
    index, _ = load_json(resource_root() / "reactions" / "index.json")
    relative = index.get(REACTION) if isinstance(index, dict) else None
    if relative != "construction/protocol.json":
        raise IsotopeError("protocol-invalid", "The Construction protocol is absent from the reaction catalog.", EXIT_MALFORMED)
    value, _ = load_json(resource_root() / "reactions" / relative)
    if value.get("id") != REACTION or value.get("version") != "1" or value.get("transport") != "native-only":
        raise IsotopeError("protocol-invalid", "The Construction protocol root is invalid.", EXIT_MALFORMED)
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
            "The requested host/model does not resolve to one enabled Construction option.",
            EXIT_REFUSED,
            {"host": host, "model": model, "candidates": [{"host": item["host"], "model": item["model"]} for item in choices], "next_action": "update .isotope/registry.json or select an exact model"},
        )
    return choices[0]["model"]


def _coordinates(project, slug: str | None, change: int | None, round_number: int | None) -> dict[str, Any]:
    operating = read_operating(project)
    if slug is None:
        if operating is None:
            raise IsotopeError("no-armed-operation", "An unarmed Construction requires an explicit slug.", EXIT_REFUSED)
        slug = operating["slug"]
    if change is None or round_number is None or change < 1 or round_number < 1:
        raise IsotopeError("usage", "Construction requires positive --change and --round coordinates.", EXIT_USAGE)
    return {"slug": slug, "change": change, "round": round_number}


def _resource_revision(path: Path) -> str:
    return bytes_revision(path.read_bytes())


def _source_revisions(project, specimen_revision: str, operating: dict[str, Any]) -> dict[str, str]:
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
    }


def _latest_rework_findings(value: dict[str, Any], change: int, prior_round: dict[str, Any] | None) -> list[str]:
    if prior_round is None:
        return []
    assay = next(
        (item for item in value["assays"] if item["change"] == change and item["round"] == prior_round["number"]),
        None,
    )
    if assay is not None and assay["outcome"] == "CHANGES":
        return list(assay["findings"])
    if assay is not None and assay["outcome"] == "PASS" and value["acceptances"]:
        acceptance = value["acceptances"][-1]
        if acceptance["verdict"] == "CHANGES":
            return [item["text"] for item in acceptance["findings"] if item["change"] == change]
    return []


def _resolve(project, coordinates: dict[str, Any]) -> dict[str, Any]:
    located = specimens.locate(project, coordinates["slug"])
    if located.stage != "flux":
        raise IsotopeError("wrong-stage", "Construction requires a flux specimen.", EXIT_REFUSED, {"stage": located.stage})
    value, specimen_revision = specimens.read_validated(located)
    operating = read_operating(project)
    if operating is None or operating["slug"] != coordinates["slug"] or operating["specimen_revision"] != specimen_revision:
        raise IsotopeError("no-armed-operation", "Construction requires the exact armed specimen revision.", EXIT_REFUSED, {"slug": coordinates["slug"]})
    if gitops.current_branch(project) != operating["branch"]:
        raise IsotopeError("operating-drift", "Construction requires the armed branch to be current.", EXIT_CONFLICT)
    changes = [item for item in value["changes"] if item["number"] == coordinates["change"]]
    if len(changes) != 1:
        raise IsotopeError("change-not-found", "Construction change must resolve exactly once.", EXIT_NOT_FOUND, {"change": coordinates["change"]})
    rounds = sorted((item for item in value["rounds"] if item["change"] == coordinates["change"]), key=lambda item: item["number"])
    destination = next((item for item in rounds if item["number"] == coordinates["round"]), None)
    expected_round = len(rounds) + 1
    if destination is None and coordinates["round"] != expected_round:
        raise IsotopeError("round-not-ready", "Construction must target the next contiguous round.", EXIT_REFUSED, {"requested": coordinates["round"], "expected": expected_round})
    prior_round = rounds[-1] if rounds and destination is None else (rounds[-2] if len(rounds) > 1 and destination is rounds[-1] else None)
    findings = _latest_rework_findings(value, coordinates["change"], prior_round)
    if destination is None and prior_round is not None and prior_round["status"] == "complete" and not findings:
        raise IsotopeError("round-not-ready", "A later Construction round requires causal Review or Acceptance findings.", EXIT_REFUSED, {"round": coordinates["round"]})
    manifest_value, _ = manifest.load(project)
    gate_ids = [item["gate_id"] for item in value.get("verification", []) if item.get("gate_id")]
    missing_gates = [gate_id for gate_id in gate_ids if gate_id not in manifest_value.get("gates", {})]
    if missing_gates:
        raise IsotopeError("gate-not-found", "Every Construction verification gate must resolve through the manifest.", EXIT_REFUSED, {"gates": missing_gates})
    return {
        "located": located,
        "specimen": value,
        "specimen_revision": specimen_revision,
        "operating": operating,
        "change": changes[0],
        "prior_round": prior_round,
        "findings": findings,
        "gates": {gate_id: manifest_value["gates"][gate_id] for gate_id in gate_ids},
        "destination": destination,
        "source_revisions": _source_revisions(project, specimen_revision, operating),
    }


def _slot_metadata(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    value = resolved["specimen"]
    counts = {
        "specimen": 1,
        "goal": 1,
        "context": 1,
        "change": 1,
        "criteria": len(value.get("acceptance_criteria", [])),
        "decisions": len(value["decisions"]),
        "prior_round": 0 if resolved["prior_round"] is None else 1,
        "findings": len(resolved["findings"]),
        "verification": len(value.get("verification", [])),
        "gates": len(resolved["gates"]),
        "operating": 1,
        "answers": 0,
        "result_destination": 0 if resolved["destination"] is None else 1,
    }
    return [{"id": slot["id"], "ready": True, "count": counts[slot["id"]], "selector": slot["selector"]} for slot in brief_map()["slots"]]


def inspect(project, slug: str | None, *, host: str | None, model: str | None, change: int | None, round_number: int | None, after: str | None) -> dict[str, Any]:
    try:
        coordinates = _coordinates(project, slug, change, round_number)
        if host is not None:
            _select_option(project, host, model)
            require_ready(project, host)
        resolved = _resolve(project, coordinates)
        if resolved["destination"] is None and resolved["prior_round"] is not None and resolved["prior_round"]["status"] != "complete":
            prior_invocation = resolved["prior_round"].get("invocation_id")
            if after is None or after != prior_invocation:
                return {"state": "not-ready", "reason": "predecessor-required", "coordinates": coordinates, "details": {"after": prior_invocation}, "next_action": f"continue with --after {prior_invocation}"}
        if after is not None:
            predecessor = invocations.read_invocation(project, after)
            unanswered = [item["id"] for item in predecessor["questions"] if item.get("answer") is None]
            if unanswered:
                return {"state": "needs-answer", "coordinates": coordinates, "missing": unanswered, "next_action": f"answer questions on {after}"}
            if predecessor["status"] == "blocked" and predecessor["blocking_condition"] is not None:
                facts = predecessor["blocking_condition"]["observed_state"]["facts"]
                current_state = revision({key: value for key, value in resolved["source_revisions"].items() if key not in ("specimen", "operating")})
                _, workspace_revision = gitops.review_snapshot(project, resolved["operating"]["base_commit"])
                if facts.get("construction_state") == current_state and facts.get("workspace") == workspace_revision:
                    return {"state": "blocked", "coordinates": coordinates, "condition": predecessor["blocking_condition"]["condition"], "fingerprint": predecessor["blocking_condition"]["observed_state"]["fingerprint"], "next_action": "change the named condition before continuing"}
        if resolved["destination"] is not None:
            entity = resolved["destination"]
            return {"state": "complete", "coordinates": coordinates, "outcome": entity["status"], "entity": {"kind": "round", "id": entity["id"], "revision": revision(entity)}, "slots": _slot_metadata(resolved)}
        return {"state": "ready", "coordinates": coordinates, "slots": _slot_metadata(resolved), "source_revisions": resolved["source_revisions"]}
    except IsotopeError as exc:
        if exc.code == "usage":
            raise
        return {"state": "not-ready", "reason": exc.code, "details": exc.details, "next_action": "repair the named readiness condition and inspect again"}


def open_invocation(project, slug: str | None, *, host: str, model: str | None, change: int | None, round_number: int | None, after: str | None) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    result = inspect(project, slug, host=host, model=selected_model, change=change, round_number=round_number, after=after)
    if result["state"] == "complete":
        return {"result": {"status": "complete", "outcome": result["outcome"], "entity": result["entity"]}, "invocation_id": None, "completion_capability": None}
    if result["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Construction cannot open until inspection is ready.", EXIT_REFUSED, result)
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
        raise IsotopeError("stale-construction-source", "A frozen Construction source changed after open.", EXIT_CONFLICT, {"sources": mismatches})
    return resolved


def brief(project, invocation_id: str) -> dict[str, Any]:
    record = invocations.read_invocation(project, invocation_id)
    if record["reaction"] != REACTION or record["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind this Construction protocol.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-briefable", "Only an open Construction invocation can pull a brief.", EXIT_CONFLICT, {"status": record["status"]})
    resolved = _assert_sources(project, record)
    answers = []
    if record["predecessor"] is not None:
        predecessor = invocations.read_invocation(project, record["predecessor"])
        answers = [{"question_id": item["id"], "answer": item["answer"]} for item in predecessor["questions"] if item.get("answer") is not None]
    value = resolved["specimen"]
    return {
        "schema_version": "1",
        "reaction": REACTION,
        "protocol_version": record["protocol_version"],
        "invocation_id": record["id"],
        "coordinates": record["coordinates"],
        "source_revisions": record["source_revisions"],
        "values": {
            "goal": value["goal"],
            "context": value["context"],
            "change": resolved["change"],
            "criteria": value.get("acceptance_criteria", []),
            "decisions": value["decisions"],
            "prior_round": resolved["prior_round"],
            "findings": resolved["findings"],
            "verification": value.get("verification", []),
            "gates": resolved["gates"],
            "operating": {key: resolved["operating"][key] for key in ("slug", "branch", "base_commit", "specimen_revision")},
            "answers": answers,
            "result_destination": None,
        },
        "record_command": f"python .isotope/bin/isotope.py agent record construction --invocation {record['id']} --input <json-file>",
    }


def _validate_readout(value: Any) -> dict[str, Any]:
    from .schemas import _validate

    _validate(readout_schema(), value)
    status = value["status"]
    if status == "complete" and (value["questions"] or value["blockers"]):
        raise IsotopeError("result-malformed", "A complete Construction readout has no questions or blockers.", EXIT_MALFORMED)
    if status == "needs-user" and (not value["questions"] or value["blockers"]):
        raise IsotopeError("result-malformed", "needs-user requires questions and no blockers.", EXIT_MALFORMED)
    if status == "blocked" and (not value["blockers"] or value["questions"]):
        raise IsotopeError("result-malformed", "blocked requires blockers and no questions.", EXIT_MALFORMED)
    return value


def _round_entity(invocation: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    coordinates = invocation["coordinates"]
    return {
        "schema_version": "2",
        "id": f"C{coordinates['change']}-R{coordinates['round']}",
        "change": coordinates["change"],
        "number": coordinates["round"],
        "abstract": value["abstract"],
        "status": {"complete": "complete", "needs-user": "decision-needed", "blocked": "blocked"}[value["status"]],
        "details": value["details"],
        "files_touched": value["files_touched"],
        "evidence": value["evidence"],
        "decision_questions": value["questions"],
        "blockers": value["blockers"],
        "invocation_id": invocation["id"],
        "reaction_protocol_version": invocation["protocol_version"],
        "source_revisions": invocation["source_revisions"],
        "actor": {"host": invocation["host"], "model": invocation["model"], "reaction": REACTION},
    }


def record(project, invocation_id: str, readout: Any) -> dict[str, Any]:
    record_value = invocations.read_invocation(project, invocation_id)
    if record_value["reaction"] != REACTION:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind Construction.", EXIT_CONFLICT)
    if os.environ.get("ISOTOPE_HOST") != record_value["host"]:
        raise IsotopeError("authority-unavailable", "The active native host does not match the Construction invocation.", EXIT_REFUSED, {"host": record_value["host"]})
    value = _validate_readout(readout)
    entity = _round_entity(record_value, value)
    if record_value["status"] in ("complete", "needs-user", "blocked") and record_value["result"] is not None:
        located = specimens.locate(project, record_value["coordinates"]["slug"])
        specimen, _ = specimens.read_validated(located)
        existing = next((item for item in specimen["rounds"] if item.get("invocation_id") == invocation_id), None)
        comparable = None if existing is None else {key: item for key, item in existing.items() if key != "review_snapshot"}
        if comparable != entity:
            raise IsotopeError("round-race", "The invocation already recorded a different Construction result.", EXIT_CONFLICT, {"invocation": invocation_id})
        return {"invocation_id": invocation_id, "result": record_value["result"]}
    resolved = _assert_sources(project, record_value)
    coordinates = record_value["coordinates"]
    _, _, _, compact = specimens.record_construction(
        project,
        coordinates["slug"],
        expected_revision=resolved["specimen_revision"],
        reason="Recorded the invocation-bound Construction round.",
        entity=entity,
        source_guard=lambda: _assert_sources(project, record_value),
    )
    result = {"invocation_id": invocation_id, "result": compact}
    if value["status"] == "needs-user":
        result["questions"] = [{"id": f"Q{index}", "text": text} for index, text in enumerate(value["questions"], 1)]
    if value["status"] == "blocked":
        result["condition"] = value["blockers"][0]
        result["next_action"] = "change the named blocking condition, then open the next round with --after"
    return result


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
