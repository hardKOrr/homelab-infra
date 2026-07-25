"""Native Acceptance protocol mapping, briefing, and durable result recording."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from . import gitops, invocations, manifest, specimens
from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .operating import read_operating
from .revisions import bytes_revision, load_json, revision
from .setup import load_registry, load_synthesis, require_ready, resource_root


REACTION = "acceptance"


def _reaction_root() -> Path:
    return resource_root() / "reactions" / REACTION


def protocol() -> dict[str, Any]:
    index, _ = load_json(resource_root() / "reactions" / "index.json")
    relative = index.get(REACTION) if isinstance(index, dict) else None
    if relative != "acceptance/protocol.json":
        raise IsotopeError("protocol-invalid", "The Acceptance protocol is absent from the reaction catalog.", EXIT_MALFORMED)
    value, _ = load_json(resource_root() / "reactions" / relative)
    if value.get("id") != REACTION or value.get("version") != "1" or value.get("transport") != "native-only":
        raise IsotopeError("protocol-invalid", "The Acceptance protocol root is invalid.", EXIT_MALFORMED)
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
            "The requested host/model does not resolve to one enabled Acceptance option.",
            EXIT_REFUSED,
            {"host": host, "model": model, "candidates": [{"host": item["host"], "model": item["model"]} for item in choices], "next_action": "update .isotope/registry.json or select an exact model"},
        )
    return choices[0]["model"]


def _coordinates(project, slug: str | None, acceptance_number: int | None) -> dict[str, Any]:
    operating = read_operating(project)
    if slug is None:
        if operating is None:
            raise IsotopeError("no-armed-operation", "An unarmed Acceptance requires an explicit slug.", EXIT_REFUSED)
        slug = operating["slug"]
    if not isinstance(acceptance_number, int) or isinstance(acceptance_number, bool) or acceptance_number < 1:
        raise IsotopeError("usage", "Acceptance requires a positive --acceptance number.", EXIT_USAGE)
    return {"slug": slug, "acceptance": acceptance_number}


def _resource_revision(path: Path) -> str:
    return bytes_revision(path.read_bytes())


def _source_revisions(project, specimen_revision: str, operating: dict[str, Any], snapshot_revision: str) -> dict[str, str]:
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
        "acceptance_snapshot": snapshot_revision,
        "protocol": _resource_revision(root / "protocol.json"),
        "brief_map": _resource_revision(root / "brief.map.json"),
        "readout_schema": _resource_revision(root / "readout.schema.json"),
    }


def _final_evidence(value: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rounds = []
    assays = []
    for change in value["changes"]:
        candidates = [item for item in value["rounds"] if item["change"] == change["number"]]
        if not candidates:
            raise IsotopeError("acceptance-not-ready", "Every planned change requires a completed final round.", EXIT_REFUSED, {"change": change["number"]})
        final_round = max(candidates, key=lambda item: item["number"])
        if final_round["status"] != "complete":
            raise IsotopeError("acceptance-not-ready", "Every final round must be complete.", EXIT_REFUSED, {"change": change["number"], "round": final_round["number"]})
        assay = next((item for item in value["assays"] if item["change"] == change["number"] and item["round"] == final_round["number"]), None)
        if assay is None or assay["outcome"] != "PASS":
            raise IsotopeError("acceptance-not-ready", "Every final round requires a PASS assay.", EXIT_REFUSED, {"change": change["number"], "round": final_round["number"]})
        rounds.append(final_round)
        assays.append(assay)
    return rounds, assays


def _resolve(project, coordinates: dict[str, Any]) -> dict[str, Any]:
    located = specimens.locate(project, coordinates["slug"])
    if located.stage != "flux":
        raise IsotopeError("wrong-stage", "Acceptance requires a flux specimen.", EXIT_REFUSED, {"stage": located.stage})
    value, specimen_revision = specimens.read_validated(located)
    for field in ("changes", "acceptance_criteria", "verification"):
        if not value.get(field):
            raise IsotopeError("acceptance-not-ready", "Acceptance requires declared changes, criteria, and verification.", EXIT_REFUSED, {"path": f"/{field}"})
    operating = read_operating(project)
    if operating is None or operating["slug"] != coordinates["slug"] or operating["specimen_revision"] != specimen_revision:
        raise IsotopeError("no-armed-operation", "Acceptance requires the exact armed specimen revision.", EXIT_REFUSED, {"slug": coordinates["slug"]})
    if gitops.current_branch(project) != operating["branch"]:
        raise IsotopeError("operating-drift", "Acceptance requires the armed branch to be current.", EXIT_CONFLICT)
    destination = next((item for item in value["acceptances"] if item["number"] == coordinates["acceptance"]), None)
    expected = len(value["acceptances"]) + 1
    already_complete = destination is not None and destination.get("invocation_id") is not None
    if destination is not None and not already_complete:
        raise IsotopeError("acceptance-race", "An unbound Acceptance already owns this destination.", EXIT_CONFLICT, {"acceptance": coordinates["acceptance"]})
    if destination is None and coordinates["acceptance"] != expected:
        raise IsotopeError("acceptance-not-ready", "Acceptance must target the next contiguous identity.", EXIT_REFUSED, {"requested": coordinates["acceptance"], "expected": expected})
    final_rounds, final_assays = _final_evidence(value)
    manifest_value, _ = manifest.load(project)
    gates = {}
    for check in value["verification"]:
        gate_id = check.get("gate_id")
        if gate_id is None:
            continue
        if gate_id not in manifest_value.get("gates", {}):
            raise IsotopeError("gate-not-found", "An Acceptance verification gate does not resolve in the manifest.", EXIT_REFUSED, {"verification": check["id"], "gate_id": gate_id})
        gates[gate_id] = manifest_value["gates"][gate_id]
    snapshot, snapshot_revision = gitops.review_snapshot(project, operating["base_commit"])
    return {
        "located": located,
        "specimen": value,
        "specimen_revision": specimen_revision,
        "operating": operating,
        "destination": destination,
        "already_complete": already_complete,
        "final_rounds": final_rounds,
        "final_assays": final_assays,
        "gates": gates,
        "acceptance_snapshot": snapshot,
        "source_revisions": _source_revisions(project, specimen_revision, operating, snapshot_revision),
    }


def _slot_metadata(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    value = resolved["specimen"]
    counts = {
        "specimen": 1,
        "goal": 1,
        "criteria": len(value["acceptance_criteria"]),
        "verification": len(value["verification"]),
        "changes": len(value["changes"]),
        "decisions": len(value["decisions"]),
        "final_rounds": len(resolved["final_rounds"]),
        "final_assays": len(resolved["final_assays"]),
        "gates": len(resolved["gates"]),
        "operating": 1,
        "acceptance_snapshot": 1,
        "answers": 0,
        "result_destination": 0 if resolved["destination"] is None else 1,
    }
    return [{"id": slot["id"], "ready": True, "count": counts[slot["id"]], "selector": slot["selector"]} for slot in brief_map()["slots"]]


def inspect(project, slug: str | None, *, host: str | None, model: str | None, acceptance_number: int | None, after: str | None) -> dict[str, Any]:
    try:
        coordinates = _coordinates(project, slug, acceptance_number)
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
                if facts.get("acceptance_state") == revision(resolved["source_revisions"]):
                    return {"state": "blocked", "coordinates": coordinates, "condition": predecessor["blocking_condition"]["condition"], "fingerprint": predecessor["blocking_condition"]["observed_state"]["fingerprint"], "next_action": "change the named condition before continuing"}
        if resolved["already_complete"]:
            entity = resolved["destination"]
            return {"state": "complete", "coordinates": coordinates, "outcome": entity["verdict"], "entity": {"kind": "acceptance", "id": entity["id"], "revision": revision(entity)}, "slots": _slot_metadata(resolved)}
        return {"state": "ready", "coordinates": coordinates, "slots": _slot_metadata(resolved), "source_revisions": resolved["source_revisions"]}
    except IsotopeError as exc:
        if exc.code == "usage":
            raise
        return {"state": "not-ready", "reason": exc.code, "details": exc.details, "next_action": "repair the named readiness condition and inspect again"}


def open_invocation(project, slug: str | None, *, host: str, model: str | None, acceptance_number: int | None, after: str | None) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    result = inspect(project, slug, host=host, model=selected_model, acceptance_number=acceptance_number, after=after)
    if result["state"] == "complete":
        return {"result": {"status": "complete", "outcome": result["outcome"], "entity": result["entity"]}, "invocation_id": None, "completion_capability": None}
    if result["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Acceptance cannot open until inspection is ready.", EXIT_REFUSED, result)
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
        raise IsotopeError("stale-acceptance-source", "A frozen Acceptance source changed after open.", EXIT_CONFLICT, {"sources": mismatches})
    return resolved


def brief(project, invocation_id: str) -> dict[str, Any]:
    record = invocations.read_invocation(project, invocation_id)
    if record["reaction"] != REACTION or record["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind this Acceptance protocol.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-briefable", "Only an open Acceptance invocation can pull a brief.", EXIT_CONFLICT, {"status": record["status"]})
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
            "specimen": value,
            "goal": value["goal"],
            "criteria": value["acceptance_criteria"],
            "verification": value["verification"],
            "changes": value["changes"],
            "decisions": value["decisions"],
            "final_rounds": resolved["final_rounds"],
            "final_assays": resolved["final_assays"],
            "gates": resolved["gates"],
            "operating": {key: resolved["operating"][key] for key in ("slug", "branch", "base_commit", "specimen_revision")},
            "acceptance_snapshot": resolved["acceptance_snapshot"],
            "patch": gitops.snapshot_patch(project, resolved["operating"]["base_commit"]),
            "answers": answers,
            "result_destination": resolved["destination"],
        },
        "record_command": f"python .isotope/bin/isotope.py agent record acceptance --invocation {record['id']} --input <json-file>",
    }


def _validate_readout(value: Any) -> dict[str, Any]:
    from .schemas import _validate

    _validate(readout_schema(), value)
    status = value["status"]
    if status == "complete":
        if value["verdict"] is None or value["abstract"] is None or value["questions"] or any(value[key] is not None for key in ("condition", "next_action")):
            raise IsotopeError("result-malformed", "A complete Acceptance readout requires the determination fields only.", EXIT_MALFORMED)
        has_failure = any(item["status"] == "FAIL" for item in value["criteria"] + value["verification"]) or bool(value["findings"])
        if (value["verdict"] == "PASS") == has_failure:
            raise IsotopeError("result-malformed", "The Acceptance verdict must match its checks and findings.", EXIT_MALFORMED)
    elif status == "needs-user":
        if not value["questions"] or value["verdict"] is not None or value["abstract"] is not None or value["next_action"] is None:
            raise IsotopeError("result-malformed", "needs-user requires questions and a next action.", EXIT_MALFORMED)
    elif status == "blocked":
        if value["condition"] is None or value["next_action"] is None or value["verdict"] is not None or value["abstract"] is not None:
            raise IsotopeError("result-malformed", "blocked requires condition, facts, and next_action.", EXIT_MALFORMED)
    elif value["next_action"] is None or value["verdict"] is not None or value["abstract"] is not None:
        raise IsotopeError("result-malformed", f"{status} requires a next action and no determination payload.", EXIT_MALFORMED)
    return value


def _assert_check_identities(resolved: dict[str, Any], value: dict[str, Any]) -> None:
    expected_criteria = [item["id"] for item in resolved["specimen"]["acceptance_criteria"]]
    expected_verification = [item["id"] for item in resolved["specimen"]["verification"]]
    observed_criteria = [item["criterion_id"] for item in value["criteria"]]
    observed_verification = [item["verification_id"] for item in value["verification"]]
    if observed_criteria != expected_criteria or observed_verification != expected_verification:
        raise IsotopeError("acceptance-check-mismatch", "Acceptance must check every declared identity once in declaration order.", EXIT_CONFLICT, {"criteria": {"expected": expected_criteria, "actual": observed_criteria}, "verification": {"expected": expected_verification, "actual": observed_verification}})


def _acceptance_entity(invocation: dict[str, Any], resolved: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    number = invocation["coordinates"]["acceptance"]
    return {
        "schema_version": "2",
        "id": f"A{number}",
        "number": number,
        "verdict": value["verdict"],
        "abstract": value["abstract"],
        "criteria": value["criteria"],
        "verification": value["verification"],
        "findings": value["findings"],
        "acceptance_snapshot": resolved["acceptance_snapshot"],
        "invocation_id": invocation["id"],
        "reaction_protocol_version": invocation["protocol_version"],
        "source_revisions": invocation["source_revisions"],
        "actor": {"host": invocation["host"], "model": invocation["model"], "reaction": REACTION},
    }


def record(project, invocation_id: str, readout: Any) -> dict[str, Any]:
    record_value = invocations.read_invocation(project, invocation_id)
    if record_value["reaction"] != REACTION:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind Acceptance.", EXIT_CONFLICT)
    if os.environ.get("ISOTOPE_HOST") != record_value["host"]:
        raise IsotopeError("authority-unavailable", "The active native host does not match the Acceptance invocation.", EXIT_REFUSED, {"host": record_value["host"]})
    value = _validate_readout(readout)
    if record_value["status"] in ("needs-user", "blocked", "refused", "failed") and record_value["result"] is not None:
        return {"invocation_id": invocation_id, "result": record_value["result"]}
    if record_value["status"] == "complete" and record_value["result"] is not None:
        located = specimens.locate(project, record_value["coordinates"]["slug"])
        specimen, _ = specimens.read_validated(located)
        existing = next((item for item in specimen["acceptances"] if item.get("invocation_id") == invocation_id), None)
        if value["status"] != "complete" or existing is None:
            raise IsotopeError("acceptance-race", "The invocation already recorded a different Acceptance result.", EXIT_CONFLICT, {"invocation": invocation_id})
        comparable = _acceptance_entity(record_value, {"acceptance_snapshot": existing["acceptance_snapshot"]}, value)
        if comparable != existing:
            raise IsotopeError("acceptance-race", "The invocation already recorded a different Acceptance result.", EXIT_CONFLICT, {"invocation": invocation_id})
        return {"invocation_id": invocation_id, "result": record_value["result"]}
    resolved = _assert_sources(project, record_value)
    status = value["status"]
    if status == "needs-user":
        questions = [{"id": f"Q{index}", "text": text, "answer": None} for index, text in enumerate(value["questions"], 1)]
        compact = {"status": "needs-user", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="needs-user", questions=questions, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "questions": [{"id": item["id"], "text": item["text"]} for item in questions]}
    if status == "blocked":
        facts = {**value["facts"], "acceptance_state": revision(record_value["source_revisions"])}
        condition = {"condition": value["condition"], "observed_state": {"facts": facts, "fingerprint": revision(facts)}}
        compact = {"status": "blocked", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="blocked", blocking_condition=condition, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "condition": value["condition"], "next_action": value["next_action"]}
    if status in ("refused", "failed"):
        compact = {"status": status, "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status=status, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "next_action": value["next_action"]}
    _assert_check_identities(resolved, value)
    entity = _acceptance_entity(record_value, resolved, value)
    _, _, _, compact = specimens.record_acceptance(
        project,
        record_value["coordinates"]["slug"],
        expected_revision=resolved["specimen_revision"],
        reason="Recorded the invocation-bound whole-specimen Acceptance determination.",
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
