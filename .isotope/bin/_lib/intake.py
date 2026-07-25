"""Native Intake protocol mapping, briefing, and durable matter recording."""

from __future__ import annotations

import os
import secrets
from pathlib import Path, PurePosixPath
from typing import Any

from . import invocations, manifest, specimens
from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .paths import CULTURES_DIR, ISOTOPE_DIR
from .revisions import bytes_revision, load_json, revision
from .setup import load_registry, load_synthesis, require_ready, resource_root


REACTION = "intake"


def _reaction_root() -> Path:
    return resource_root() / "reactions" / REACTION


def protocol() -> dict[str, Any]:
    index, _ = load_json(resource_root() / "reactions" / "index.json")
    relative = index.get(REACTION) if isinstance(index, dict) else None
    if relative != "intake/protocol.json":
        raise IsotopeError("protocol-invalid", "The Intake protocol is absent from the reaction catalog.", EXIT_MALFORMED)
    value, _ = load_json(resource_root() / "reactions" / relative)
    if value.get("id") != REACTION or value.get("version") != "1" or value.get("transport") != "native-only":
        raise IsotopeError("protocol-invalid", "The Intake protocol root is invalid.", EXIT_MALFORMED)
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
            "The requested host/model does not resolve to one enabled Intake option.",
            EXIT_REFUSED,
            {"host": host, "model": model, "candidates": [{"host": item["host"], "model": item["model"]} for item in choices], "next_action": "update .isotope/registry.json or select an exact model"},
        )
    return choices[0]["model"]


def _coordinates(slug: str | None, mode: str | None, dump: str | None) -> dict[str, Any]:
    if mode not in ("capture", "rework"):
        raise IsotopeError("usage", "Intake requires --mode capture or --mode rework.", EXIT_USAGE)
    if not isinstance(dump, str) or not dump:
        raise IsotopeError("usage", "Intake requires a repo-relative --dump brain-dump path.", EXIT_USAGE)
    if mode == "rework" and slug is None:
        raise IsotopeError("usage", "Intake rework requires the matter specimen slug.", EXIT_USAGE)
    if mode == "capture" and slug is not None:
        raise IsotopeError("usage", "Intake capture chooses its slugs in the readout; give none.", EXIT_USAGE)
    coordinates = {"mode": mode, "dump": dump}
    if slug is not None:
        coordinates["slug"] = slug
    return coordinates


def _dump_source(project, relative: str) -> tuple[str, str]:
    pure = PurePosixPath(relative)
    if "\\" in relative or pure.is_absolute() or pure.as_posix() != relative or any(part in ("", ".", "..") for part in pure.parts):
        raise IsotopeError("usage", "The Intake --dump path must be a normalized repo-relative POSIX path.", EXIT_USAGE, {"path": relative})
    path = project.root / relative
    if not path.resolve().is_relative_to(project.root.resolve()) or not path.is_file():
        raise IsotopeError("dump-not-found", "The Intake brain-dump file does not resolve inside the project.", EXIT_NOT_FOUND, {"path": relative})
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IsotopeError("dump-malformed", "The Intake brain-dump is not readable UTF-8.", EXIT_MALFORMED, {"path": relative}) from exc
    if not text.strip():
        raise IsotopeError("dump-empty", "The Intake brain-dump carries no content.", EXIT_MALFORMED, {"path": relative})
    return text, bytes_revision(data)


def _resource_revision(path: Path) -> str:
    return bytes_revision(path.read_bytes())


def _source_revisions(project, dump_revision: str, cultures: dict[str, list[str]], specimen_revision: str | None) -> dict[str, str]:
    _, manifest_revision = manifest.load(project)
    _, registry_revision = load_registry(project)
    _, synthesis_revision = load_synthesis(project)
    root = _reaction_root()
    revisions = {
        "dump": dump_revision,
        "cultures": revision(cultures),
        "manifest": manifest_revision,
        "registry": registry_revision,
        "synthesis": synthesis_revision,
        "protocol": _resource_revision(root / "protocol.json"),
        "brief_map": _resource_revision(root / "brief.map.json"),
        "readout_schema": _resource_revision(root / "readout.schema.json"),
    }
    if specimen_revision is not None:
        revisions["specimen"] = specimen_revision
    return revisions


def _resolve(project, coordinates: dict[str, Any]) -> dict[str, Any]:
    dump_text, dump_revision = _dump_source(project, coordinates["dump"])
    specimen_value = None
    specimen_revision = None
    if coordinates["mode"] == "rework":
        located = specimens.locate(project, coordinates["slug"])
        if located.stage != "matter":
            raise IsotopeError("wrong-stage", "Intake rework rewrites a matter specimen in place.", EXIT_REFUSED, {"slug": coordinates["slug"], "stage": located.stage})
        specimen_value, specimen_revision = specimens.read_validated(located)
    cultures = specimens.culture_slugs(project)
    return {
        "dump": dump_text,
        "specimen": specimen_value,
        "specimen_revision": specimen_revision,
        "cultures": cultures,
        "source_revisions": _source_revisions(project, dump_revision, cultures, specimen_revision),
    }


def _slot_metadata(resolved: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    counts = {
        "dump": 1,
        "specimen": 0 if resolved["specimen"] is None else 1,
        "cultures": sum(len(slugs) for slugs in resolved["cultures"].values()),
        "answers": 0,
        "result_destination": 1 if mode == "rework" else 0,
    }
    return [{"id": slot["id"], "ready": True, "count": counts[slot["id"]], "selector": slot["selector"]} for slot in brief_map()["slots"]]


def inspect(project, slug: str | None, *, host: str | None, model: str | None, mode: str | None, dump: str | None, after: str | None) -> dict[str, Any]:
    try:
        coordinates = _coordinates(slug, mode, dump)
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
                if facts.get("intake_state") == revision(resolved["source_revisions"]):
                    return {"state": "blocked", "coordinates": coordinates, "condition": predecessor["blocking_condition"]["condition"], "fingerprint": predecessor["blocking_condition"]["observed_state"]["fingerprint"], "next_action": "change the named condition before continuing"}
        return {"state": "ready", "coordinates": coordinates, "slots": _slot_metadata(resolved, coordinates["mode"]), "source_revisions": resolved["source_revisions"]}
    except IsotopeError as exc:
        if exc.code == "usage":
            raise
        return {"state": "not-ready", "reason": exc.code, "details": exc.details, "next_action": "repair the named readiness condition and inspect again"}


def open_invocation(project, slug: str | None, *, host: str, model: str | None, mode: str | None, dump: str | None, after: str | None) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    result = inspect(project, slug, host=host, model=selected_model, mode=mode, dump=dump, after=after)
    if result["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Intake cannot open until inspection is ready.", EXIT_REFUSED, result)
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
        raise IsotopeError("stale-intake-source", "A frozen Intake source changed after open.", EXIT_CONFLICT, {"sources": mismatches})
    return resolved


def brief(project, invocation_id: str) -> dict[str, Any]:
    record = invocations.read_invocation(project, invocation_id)
    if record["reaction"] != REACTION or record["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind this Intake protocol.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-briefable", "Only an open Intake invocation can pull a brief.", EXIT_CONFLICT, {"status": record["status"]})
    resolved = _assert_sources(project, record)
    answers = []
    if record["predecessor"] is not None:
        predecessor = invocations.read_invocation(project, record["predecessor"])
        answers = [{"question_id": item["id"], "answer": item["answer"]} for item in predecessor["questions"] if item.get("answer") is not None]
    coordinates = record["coordinates"]
    if coordinates["mode"] == "rework":
        destination = specimens.culture_relative("matter", coordinates["slug"])
    else:
        destination = f"{ISOTOPE_DIR}/{CULTURES_DIR}/matter/<readout-chosen slug>.json"
    return {
        "schema_version": "1",
        "reaction": REACTION,
        "protocol_version": record["protocol_version"],
        "invocation_id": record["id"],
        "coordinates": coordinates,
        "source_revisions": record["source_revisions"],
        "values": {
            "dump": resolved["dump"],
            "specimen": resolved["specimen"],
            "cultures": resolved["cultures"],
            "answers": answers,
            "result_destination": destination,
        },
        "record_command": f"python .isotope/bin/isotope.py agent record intake --invocation {record['id']} --input <json-file>",
    }


def _validate_readout(value: Any, mode: str) -> dict[str, Any]:
    from .schemas import _validate

    _validate(readout_schema(), value)
    status = value["status"]
    if status == "complete":
        if value["questions"] or value["condition"] is not None or value["next_action"] is not None:
            raise IsotopeError("result-malformed", "A complete Intake readout carries matter payloads only.", EXIT_MALFORMED)
        if mode == "capture":
            slugs = [item["slug"] for item in value["specimens"]]
            if value["matter"] is not None or len(slugs) != len(set(slugs)):
                raise IsotopeError("result-malformed", "A complete capture yields uniquely slugged specimens and no rework matter.", EXIT_MALFORMED)
        elif value["matter"] is None or value["specimens"]:
            raise IsotopeError("result-malformed", "A complete rework rewrites the selected matter only.", EXIT_MALFORMED)
    elif status == "needs-user":
        if not value["questions"] or value["specimens"] or value["matter"] is not None or value["next_action"] is None:
            raise IsotopeError("result-malformed", "needs-user requires questions and a next action.", EXIT_MALFORMED)
    elif status == "blocked":
        if value["condition"] is None or value["next_action"] is None or value["specimens"] or value["matter"] is not None:
            raise IsotopeError("result-malformed", "blocked requires condition, facts, and next_action.", EXIT_MALFORMED)
    elif value["next_action"] is None or value["specimens"] or value["matter"] is not None:
        raise IsotopeError("result-malformed", f"{status} requires a next action and no matter payload.", EXIT_MALFORMED)
    return value


def record(project, invocation_id: str, readout: Any) -> dict[str, Any]:
    record_value = invocations.read_invocation(project, invocation_id)
    if record_value["reaction"] != REACTION or record_value["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind Intake.", EXIT_CONFLICT)
    if os.environ.get("ISOTOPE_HOST") != record_value["host"]:
        raise IsotopeError("authority-unavailable", "The active native host does not match the Intake invocation.", EXIT_REFUSED, {"host": record_value["host"]})
    mode = record_value["coordinates"]["mode"]
    value = _validate_readout(readout, mode)
    if record_value["status"] in ("needs-user", "blocked", "refused", "failed") and record_value["result"] is not None:
        return {"invocation_id": invocation_id, "result": record_value["result"]}
    if record_value["status"] == "complete" and record_value["result"] is not None and value["status"] != "complete":
        raise IsotopeError("intake-race", "The invocation already recorded a different Intake result.", EXIT_CONFLICT, {"invocation": invocation_id})
    status = value["status"]
    if status != "complete":
        _assert_sources(project, record_value)
    if status == "needs-user":
        questions = [{"id": f"Q{index}", "text": text, "answer": None} for index, text in enumerate(value["questions"], 1)]
        compact = {"status": "needs-user", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="needs-user", questions=questions, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "questions": [{"id": item["id"], "text": item["text"]} for item in questions]}
    if status == "blocked":
        facts = {**value["facts"], "intake_state": revision(record_value["source_revisions"])}
        condition = {"condition": value["condition"], "observed_state": {"facts": facts, "fingerprint": revision(facts)}}
        compact = {"status": "blocked", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="blocked", blocking_condition=condition, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "condition": value["condition"], "next_action": value["next_action"]}
    if status in ("refused", "failed"):
        compact = {"status": status, "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status=status, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "next_action": value["next_action"]}
    compact = specimens.record_intake(
        project,
        invocation_id,
        entities=value["specimens"] if mode == "capture" else None,
        matter_payload=value["matter"] if mode == "rework" else None,
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
