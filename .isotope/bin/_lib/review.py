"""Executable Review protocol mapping, briefing, completion, and host launch."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import gitops, invocations, manifest, specimens
from .execution import run_bounded
from .errors import (
    EXIT_CONFLICT,
    EXIT_MALFORMED,
    EXIT_NOT_FOUND,
    EXIT_REFUSED,
    EXIT_USAGE,
    IsotopeError,
)
from .operating import read_operating
from .locks import acquire_invocation_lease, release_invocation_lease
from .revisions import bytes_revision, canonical_bytes, load_json, revision
from .setup import load_registry, load_synthesis, require_ready, resource_root
from .schemas import validate as validate_schema


def _reaction_root() -> Path:
    return resource_root() / "reactions" / "review"


def protocol() -> dict[str, Any]:
    index, _ = load_json(resource_root() / "reactions" / "index.json")
    if not isinstance(index, dict) or index.get("review") != "review/protocol.json" or not all(isinstance(key, str) and isinstance(value, str) for key, value in index.items()):
        raise IsotopeError("protocol-invalid", "The reaction index must be an ID-to-root-path catalog only.", EXIT_MALFORMED)
    value, _ = load_json(resource_root() / "reactions" / index["review"])
    if value.get("id") != "review" or value.get("version") != "1":
        raise IsotopeError("protocol-invalid", "The Review protocol root is invalid.", EXIT_MALFORMED)
    return value


def brief_map() -> dict[str, Any]:
    value, _ = load_json(_reaction_root() / "brief.map.json")
    return value


def readout_schema() -> dict[str, Any]:
    value, _ = load_json(_reaction_root() / "readout.schema.json")
    return value


def _reaction(reaction: str) -> None:
    if reaction != "review":
        raise IsotopeError("reaction-not-found", f"Reaction {reaction!r} is not executable yet.", EXIT_NOT_FOUND, {"reaction": reaction, "available": ["review"]})


def options(project, reaction: str | None = None) -> dict[str, Any]:
    if reaction is not None:
        _reaction(reaction)
    registry, registry_revision = load_registry(project)
    choices = []
    for host, entry in sorted(registry["hosts"].items()):
        if not entry["enabled"]:
            continue
        for model in entry["models"]:
            if not model["enabled"] or (reaction is not None and reaction not in model["reactions"]):
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
    return {"reaction": reaction, "registry_revision": registry_revision, "choices": choices}


def _select_option(project, host: str, model: str | None) -> str:
    available = options(project, "review")["choices"]
    choices = [item for item in available if item["host"] == host and item["available"] is not False]
    if model is not None:
        choices = [item for item in choices if item["model"] == model]
    else:
        defaults = [item for item in choices if item["default"]]
        if len(defaults) == 1:
            choices = defaults
    if len(choices) != 1:
        raise IsotopeError(
            "registry-option-missing",
            "The requested host/model does not resolve to one enabled Review option.",
            EXIT_REFUSED,
            {"host": host, "model": model, "candidates": [{"host": item["host"], "model": item["model"]} for item in choices], "next_action": "update .isotope/registry.json or select an exact model"},
        )
    return choices[0]["model"]


def _coordinates(project, slug: str | None, change: int | None, round_number: int | None) -> dict[str, Any]:
    operating = read_operating(project)
    if slug is None:
        if operating is None:
            raise IsotopeError("no-armed-operation", "An unarmed Review requires an explicit slug.", EXIT_REFUSED)
        slug = operating["slug"]
    if change is None or round_number is None or change < 1 or round_number < 1:
        raise IsotopeError("usage", "Review requires positive --change and --round coordinates.", EXIT_USAGE)
    return {"slug": slug, "change": change, "round": round_number}


def _resource_revision(path: Path) -> str:
    return bytes_revision(path.read_bytes())


def _source_revisions(project, located, specimen_revision: str, operating: dict[str, Any]) -> dict[str, str]:
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


def _resolve(project, coordinates: dict[str, Any]) -> dict[str, Any]:
    located = specimens.locate(project, coordinates["slug"])
    if located.stage != "flux":
        raise IsotopeError("wrong-stage", "Review requires a flux specimen.", EXIT_REFUSED, {"stage": located.stage})
    value, specimen_revision = specimens.read_validated(located)
    operating = read_operating(project)
    if operating is None or operating["slug"] != coordinates["slug"] or operating["specimen_revision"] != specimen_revision:
        raise IsotopeError("no-armed-operation", "Review requires the exact armed specimen revision.", EXIT_REFUSED, {"slug": coordinates["slug"]})
    if gitops.current_branch(project) != operating["branch"] or gitops.head_commit(project) != gitops.resolve_commit(project, "HEAD"):
        raise IsotopeError("operating-drift", "The armed branch or HEAD precondition changed.", EXIT_CONFLICT)
    change = [item for item in value["changes"] if item["number"] == coordinates["change"]]
    round_records = [item for item in value["rounds"] if item["change"] == coordinates["change"] and item["number"] == coordinates["round"]]
    if len(change) != 1:
        raise IsotopeError("change-not-found", "Review change must resolve exactly once.", EXIT_NOT_FOUND, {"change": coordinates["change"]})
    if len(round_records) != 1 or round_records[0]["status"] != "complete":
        raise IsotopeError("round-not-ready", "Review round must resolve exactly once and be complete.", EXIT_REFUSED, {"round": coordinates["round"]})
    round_record = round_records[0]
    stored_snapshot = round_record.get("review_snapshot")
    if stored_snapshot is None:
        raise IsotopeError("snapshot-missing", "The completed round has no Review snapshot.", EXIT_REFUSED)
    live_snapshot, live_snapshot_revision = gitops.review_snapshot(project, operating["base_commit"])
    stored_snapshot_revision = revision(stored_snapshot)
    if live_snapshot_revision != stored_snapshot_revision:
        raise IsotopeError("stale-review-source", "Git-visible Review inputs changed after snapshot capture.", EXIT_CONFLICT, {"stored": stored_snapshot_revision, "live": live_snapshot_revision})
    manifest_value, _ = manifest.load(project)
    verification_gate_ids = {item.get("gate_id") for item in value.get("verification", []) if item.get("gate_id")}
    gates = manifest_value.get("gates", {})
    for index, evidence in enumerate(round_record["evidence"]):
        gate_id = evidence["gate_id"]
        if gate_id not in gates or gate_id not in verification_gate_ids or not isinstance(evidence["output"], str):
            raise IsotopeError("gate-evidence-unresolved", "Every Review evidence gate must resolve through manifest and verification.", EXIT_REFUSED, {"path": f"/rounds/{coordinates['round'] - 1}/evidence/{index}", "gate_id": gate_id})
    destination = next((item for item in value["assays"] if item["change"] == coordinates["change"] and item["round"] == coordinates["round"]), None)
    return {
        "located": located,
        "specimen": value,
        "specimen_revision": specimen_revision,
        "operating": operating,
        "change": change[0],
        "round": round_record,
        "snapshot": live_snapshot,
        "snapshot_revision": live_snapshot_revision,
        "destination": destination,
        "source_revisions": _source_revisions(project, located, specimen_revision, operating),
    }


def inspect(
    project,
    reaction: str,
    slug: str | None,
    *,
    host: str | None,
    model: str | None,
    change: int | None,
    round_number: int | None,
    after: str | None,
) -> dict[str, Any]:
    _reaction(reaction)
    try:
        coordinates = _coordinates(project, slug, change, round_number)
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
                observed = predecessor["blocking_condition"]["observed_state"]
                current_review_state = revision({"sources": resolved["source_revisions"], "snapshot": resolved["snapshot_revision"]})
                if observed["facts"].get("review_state") == current_review_state:
                    return {"state": "blocked", "coordinates": coordinates, "condition": predecessor["blocking_condition"]["condition"], "fingerprint": observed["fingerprint"], "next_action": "change the named condition before continuing"}
        if resolved["destination"] is not None:
            assay = resolved["destination"]
            return {"state": "complete", "coordinates": coordinates, "outcome": assay["outcome"], "entity": {"kind": "assay", "id": assay["id"], "revision": revision(assay)}, "slots": _slot_metadata(resolved)}
        return {"state": "ready", "coordinates": coordinates, "slots": _slot_metadata(resolved), "source_revisions": resolved["source_revisions"], "review_snapshot_revision": resolved["snapshot_revision"]}
    except IsotopeError as exc:
        if exc.code in ("usage", "reaction-not-found"):
            raise
        return {"state": "not-ready", "reason": exc.code, "details": exc.details, "next_action": _next_action(exc.code)}


def _slot_metadata(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    specimen = resolved["specimen"]
    counts = {
        "specimen": 1, "goal": 1, "change": 1,
        "criteria": len(specimen.get("acceptance_criteria", [])),
        "decisions": len(specimen["decisions"]), "round": 1,
        "gate_evidence": len(resolved["round"]["evidence"]), "operating": 1,
        "review_snapshot": 1, "answers": 0,
        "result_destination": 0 if resolved["destination"] is None else 1,
    }
    return [
        {"id": slot["id"], "ready": True, "count": counts[slot["id"]], "selector": slot["selector"]}
        for slot in brief_map()["slots"]
    ]


def _next_action(code: str) -> str:
    return {
        "synthesis-stale": "run isotope setup sync",
        "distribution-stale": "update the installed Isotope distribution",
        "host-unobserved": "run isotope setup observe from the active host adapter",
        "registry-option-missing": "enable an exact host/model in .isotope/registry.json",
        "stale-review-source": "record a fresh completed Construction round",
    }.get(code, "repair the named readiness condition and inspect again")


def open_invocation(project, reaction: str, slug: str | None, *, host: str, model: str | None, change: int | None, round_number: int | None, after: str | None) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    result = inspect(project, reaction, slug, host=host, model=selected_model, change=change, round_number=round_number, after=after)
    if result["state"] == "complete":
        return {"result": {"status": "complete", "outcome": result["outcome"], "entity": result["entity"]}, "invocation_id": None, "completion_capability": None}
    if result["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Review cannot open until inspection is ready.", EXIT_REFUSED, result)
    capability = secrets.token_urlsafe(32)
    record = invocations.create_invocation(
        project,
        reaction="review",
        protocol_version=protocol()["version"],
        coordinates=result["coordinates"],
        host=host,
        model=selected_model,
        predecessor=after,
        source_revisions=result["source_revisions"],
        review_snapshot_revision=result["review_snapshot_revision"],
        allowed_effects=protocol()["allowed_effects"],
        completion_capability_hash=invocations.capability_hash(capability),
    )
    return {"invocation_id": record["id"], "completion_capability": capability, "result": None}


def _assert_sources(project, invocation: dict[str, Any]) -> dict[str, Any]:
    require_ready(project, invocation["host"])
    resolved = _resolve(project, invocation["coordinates"])
    mismatches = {
        key: {"frozen": value, "current": resolved["source_revisions"].get(key)}
        for key, value in invocation["source_revisions"].items()
        if resolved["source_revisions"].get(key) != value
    }
    if mismatches or resolved["snapshot_revision"] != invocation["review_snapshot_revision"]:
        raise IsotopeError("stale-review-source", "A frozen Review source changed after open.", EXIT_CONFLICT, {"sources": mismatches, "snapshot": {"frozen": invocation["review_snapshot_revision"], "current": resolved["snapshot_revision"]}})
    return resolved


def brief(project, reaction: str, invocation_id: str) -> dict[str, Any]:
    _reaction(reaction)
    record = invocations.read_invocation(project, invocation_id)
    if record["reaction"] != "review" or record["protocol_version"] != protocol()["version"]:
        raise IsotopeError("invocation-mismatch", "The invocation does not bind this Review protocol.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-briefable", "Only an open Review invocation can pull a brief.", EXIT_CONFLICT, {"status": record["status"]})
    resolved = _assert_sources(project, record)
    answers = []
    if record["predecessor"] is not None:
        predecessor = invocations.read_invocation(project, record["predecessor"])
        answers = [{"question_id": item["id"], "answer": item["answer"]} for item in predecessor["questions"] if item.get("answer") is not None]
    value = resolved["specimen"]
    return {
        "schema_version": "1",
        "reaction": "review",
        "protocol_version": record["protocol_version"],
        "invocation_id": record["id"],
        "coordinates": record["coordinates"],
        "source_revisions": record["source_revisions"],
        "review_snapshot_revision": record["review_snapshot_revision"],
        "values": {
            "goal": value["goal"],
            "change": resolved["change"],
            "criteria": value.get("acceptance_criteria", []),
            "decisions": value["decisions"],
            "round": resolved["round"],
            "gate_evidence": resolved["round"]["evidence"],
            "operating": {key: resolved["operating"][key] for key in ("slug", "branch", "base_commit", "specimen_revision")},
            "review_snapshot": resolved["snapshot"],
            "snapshot_patch": gitops.snapshot_patch(project, resolved["operating"]["base_commit"]),
            "answers": answers,
            "result_destination": None,
        },
    }


def _validate_readout(value: Any) -> dict[str, Any]:
    schema = readout_schema()
    from .schemas import _validate  # one validator owns JSON-pointer errors
    _validate(schema, value)
    status = value["status"]
    if status == "complete":
        required = {"schema_version", "status", "outcome", "abstract", "findings"}
        if not required.issubset(value) or (value["outcome"] == "PASS" and value["findings"]) or (value["outcome"] == "CHANGES" and not value["findings"]):
            raise IsotopeError("result-malformed", "A complete Review readout has inconsistent outcome/findings fields.", EXIT_MALFORMED, {"path": "/findings"})
    elif status == "needs-user":
        if not value.get("questions"):
            raise IsotopeError("result-malformed", "needs-user requires at least one exact question.", EXIT_MALFORMED, {"path": "/questions"})
    elif status == "blocked":
        if not all(key in value for key in ("condition", "facts", "next_action")):
            raise IsotopeError("result-malformed", "blocked requires condition, facts, and next_action.", EXIT_MALFORMED)
    elif not all(key in value for key in ("abstract", "next_action")):
        raise IsotopeError("result-malformed", f"{status} requires abstract and next_action.", EXIT_MALFORMED)
    return value


def _decode_host_readout(candidate: Any) -> Any:
    if not isinstance(candidate, str):
        return candidate
    text = candidate.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[len("```json\n"):-3].strip()
    return json.loads(text)


def _codex_output_schema() -> dict[str, Any]:
    # Codex strict output schemas require every property enumerated and closed,
    # so `facts` is pinned to an empty object; a Codex blocked readout carries
    # only the broker-injected review_state fact in its fingerprint.
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "enum": ["1"]},
            "status": {"type": "string", "enum": ["complete", "needs-user", "blocked", "refused", "failed"]},
            "outcome": {"type": ["string", "null"], "enum": ["PASS", "CHANGES", None]},
            "abstract": nullable_string,
            "findings": {"type": "array", "items": {"type": "string"}},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
            "condition": nullable_string,
            "facts": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "next_action": nullable_string,
        },
        "required": ["schema_version", "status", "outcome", "abstract", "findings", "questions", "condition", "facts", "next_action"],
        "additionalProperties": False,
    }


def _normalize_host_readout(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if item is not None}


def _argument_value(arguments: list[str], flag: str) -> str | None:
    try:
        return arguments[arguments.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def _load_launch_descriptor(host: str) -> dict[str, Any]:
    descriptor, _ = load_json(resource_root() / "targets" / host / "launch.json")
    required = {
        "schema_version", "host", "adapter_version", "executable", "preflight",
        "authority", "arguments", "model_arguments", "result_kind",
    }
    if not isinstance(descriptor, dict) or set(descriptor) != required or descriptor.get("schema_version") != "1" or descriptor.get("host") != host:
        raise IsotopeError(
            "authority-unavailable",
            "The target host launch descriptor is incomplete or does not match the requested host.",
            EXIT_REFUSED,
            {"host": host, "next_action": "install a hash-valid Isotope target adapter"},
        )
    if not isinstance(descriptor["executable"], str) or not descriptor["executable"]:
        raise IsotopeError("authority-unavailable", "The target adapter has no executable identity.", EXIT_REFUSED, {"host": host})
    if (
        not isinstance(descriptor["arguments"], list)
        or not isinstance(descriptor["model_arguments"], list)
        or not all(isinstance(item, str) and item for item in descriptor["arguments"] + descriptor["model_arguments"])
    ):
        raise IsotopeError("authority-unavailable", "The target adapter arguments are invalid.", EXIT_REFUSED, {"host": host})
    preflight = descriptor["preflight"]
    authority = descriptor["authority"]
    if (
        not isinstance(preflight, dict)
        or set(preflight) != {"arguments", "result_kind", "sandbox_arguments"}
        or not isinstance(preflight["arguments"], list)
        or not all(isinstance(item, str) and item for item in preflight["arguments"])
        or not isinstance(preflight["sandbox_arguments"], list)
        or not all(isinstance(item, str) and item for item in preflight["sandbox_arguments"])
        or preflight["result_kind"] not in ("claude-auth-json", "exit-zero")
        or authority != {
            "read_only": True,
            "non_interactive": True,
            "structured_output": True,
            "explicit_agent": "isotope-review" if host == "claude" else "isotope_review",
        }
    ):
        raise IsotopeError(
            "authority-unavailable",
            "The target adapter does not declare the complete read-only, non-interactive authority profile.",
            EXIT_REFUSED,
            {"host": host, "next_action": "install the current Isotope target adapter"},
        )
    arguments = descriptor["arguments"]
    if host == "claude":
        required_facts = (
            arguments[:1] == ["-p"]
            and _argument_value(arguments, "--agent") == authority["explicit_agent"]
            and _argument_value(arguments, "--output-format") == "json"
            and _argument_value(arguments, "--json-schema") == "{readout_schema_json}"
            and _argument_value(arguments, "--permission-mode") == "dontAsk"
            and "--no-session-persistence" in arguments
            and "Edit,Write,NotebookEdit" == _argument_value(arguments, "--disallowedTools")
            and _argument_value(arguments, "--allowedTools")
            == "Bash(python .isotope/bin/isotope.py agent brief review --invocation {invocation_id})"
            and descriptor["result_kind"] == "claude-json"
        )
    else:
        required_facts = (
            arguments[:1] == ["exec"]
            and "--ephemeral" in arguments
            and _argument_value(arguments, "--sandbox") == "read-only"
            and _argument_value(arguments, "-c") == 'approval_policy="never"'
            and _argument_value(arguments, "--output-schema") == "{readout_schema_path}"
            and _argument_value(arguments, "--output-last-message") == "{result_path}"
            and _argument_value(arguments, "--cd") == "{project}"
            and descriptor["result_kind"] == "last-message-json"
        )
    if not required_facts:
        raise IsotopeError(
            "authority-unavailable",
            "The target adapter cannot prove the required read-only, non-interactive launch boundary.",
            EXIT_REFUSED,
            {"host": host, "next_action": "install the current Isotope target adapter"},
        )
    return descriptor


def _preflight_host(project, descriptor: dict[str, Any], timeout: float) -> None:
    executable = descriptor["executable"]
    preflight = descriptor["preflight"]
    if shutil.which(executable) is None:
        raise IsotopeError(
            "target-unavailable",
            "The Review host executable is unavailable.",
            EXIT_NOT_FOUND,
            {"host": descriptor["host"], "next_action": f"install {executable} and retry"},
        )
    command = [executable, *preflight["arguments"]]
    try:
        process = subprocess.run(
            command,
            cwd=project.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=min(timeout, 15.0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsotopeError(
            "target-unavailable",
            "The Review host authentication preflight could not run.",
            EXIT_NOT_FOUND,
            {"host": descriptor["host"], "next_action": "repair the installed host CLI and retry"},
        ) from exc
    authenticated = process.returncode == 0
    if authenticated and preflight["result_kind"] == "claude-auth-json":
        try:
            status = json.loads(process.stdout)
            authenticated = isinstance(status, dict) and status.get("loggedIn") is True
        except json.JSONDecodeError:
            authenticated = False
    if not authenticated:
        raise IsotopeError(
            "target-authentication-failed",
            "The Review host is not authenticated for non-interactive execution.",
            EXIT_REFUSED,
            {"host": descriptor["host"], "next_action": f"authenticate {executable} and retry"},
        )
    sandbox_arguments = preflight["sandbox_arguments"]
    if sandbox_arguments and os.name == "nt":
        rendered = [item.replace("{project}", str(project.root)) for item in sandbox_arguments]
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            sandbox = subprocess.run(
                [executable, *rendered],
                cwd=project.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=min(timeout, 15.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsotopeError(
                "authority-unavailable",
                "The target host read-only sandbox liveness probe could not run.",
                EXIT_REFUSED,
                {
                    "host": descriptor["host"],
                    "next_action": 'set [windows] sandbox = "unelevated" in %USERPROFILE%\\.codex\\config.toml, restart Codex, then retry',
                },
            ) from exc
        if sandbox.returncode != 0:
            raise IsotopeError(
                "authority-unavailable",
                "The target host cannot execute the required brief command inside its read-only sandbox.",
                EXIT_REFUSED,
                {
                    "host": descriptor["host"],
                    "next_action": 'set [windows] sandbox = "unelevated" in %USERPROFILE%\\.codex\\config.toml, restart Codex, then retry',
                },
            )


def _run_host_process(command: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return run_bounded(command, cwd=cwd, env=env, timeout=timeout)


_AUTHENTICATION_MARKERS = ("not logged in", "not authenticated", "authentication failed", "please run /login", "invalid api key", "oauth")
_AUTHORITY_MARKERS = ("approval required", "requires approval", "permission denied", "permission required", "not allowed")
_MODEL_MARKERS = ("unknown model", "model not found", "invalid model", "unsupported model", "model is not available", "model unavailable")


def _transport_failure_code(stderr: str) -> tuple[str, str, int, str]:
    message = stderr.lower()
    if any(marker in message for marker in _AUTHENTICATION_MARKERS):
        return ("target-authentication-failed", "The Review host lost authentication before completing.", EXIT_REFUSED, "authenticate the target host and launch a fresh catalyst")
    if any(marker in message for marker in _AUTHORITY_MARKERS):
        return ("authority-unavailable", "The Review host requested authority outside the non-interactive profile.", EXIT_REFUSED, "repair the read-only authority profile and launch a fresh catalyst")
    if any(marker in message for marker in _MODEL_MARKERS):
        return ("target-unavailable", "The selected Review model is unavailable at the target host.", EXIT_NOT_FOUND, "select an available registered model and launch a fresh catalyst")
    return ("target-failed", "The Review host exited before returning a readout.", EXIT_CONFLICT, "repair the named target-host condition and launch a fresh catalyst")


def _mark_transport_failed(project, invocation_id: str) -> dict[str, Any] | None:
    record = invocations.read_invocation(project, invocation_id)
    if record["status"] == "complete" and record["result"] is not None:
        return {"invocation_id": invocation_id, "result": record["result"]}
    if record["status"] in ("created", "running"):
        invocations.update_status(
            project,
            invocation_id,
            status="failed",
            result={"status": "failed", "outcome": None, "entity": None},
        )
    return None


def finish(project, reaction: str, invocation_id: str, readout: Any, completion_capability: str | None) -> dict[str, Any]:
    _reaction(reaction)
    record = invocations.read_invocation(project, invocation_id)
    value = _validate_readout(readout)
    if not completion_capability or not invocations.capability_matches(completion_capability, record["completion_capability_hash"]):
        raise IsotopeError("completion-capability-invalid", "The controlling wrapper did not provide the retained completion capability.", EXIT_REFUSED, {"invocation": invocation_id})
    if record["status"] == "complete":
        located = specimens.locate(project, record["coordinates"]["slug"])
        specimen, _ = specimens.read_validated(located)
        assay = next((item for item in specimen["assays"] if item["invocation_id"] == invocation_id), None)
        if assay is not None and value["status"] == "complete" and assay["outcome"] == value["outcome"] and assay["abstract"] == value["abstract"] and assay["findings"] == value["findings"]:
            return {"invocation_id": invocation_id, "result": record["result"]}
        raise IsotopeError("invocation-not-completable", "The invocation already completed with a different durable result.", EXIT_CONFLICT)
    if record["status"] not in ("created", "running"):
        raise IsotopeError("invocation-not-completable", "The invocation is already terminal.", EXIT_CONFLICT, {"status": record["status"]})
    located = specimens.locate(project, record["coordinates"]["slug"])
    current_specimen, _ = specimens.read_validated(located)
    occupied = next(
        (
            item for item in current_specimen["assays"]
            if item["change"] == record["coordinates"]["change"]
            and item["round"] == record["coordinates"]["round"]
        ),
        None,
    )
    if occupied is not None and occupied["invocation_id"] != invocation_id:
        raise IsotopeError("assay-race", "A different invocation completed this Review destination first.", EXIT_CONFLICT, {"invocation": invocation_id, "winner": occupied["invocation_id"]})
    resolved = _assert_sources(project, record)
    status = value["status"]
    if status == "needs-user":
        questions = [{"id": f"Q{index}", "text": item["text"], "answer": None} for index, item in enumerate(value["questions"], 1)]
        compact = {"status": "needs-user", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="needs-user", questions=questions, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "questions": [{"id": item["id"], "text": item["text"]} for item in questions]}
    if status == "blocked":
        facts = {**value["facts"], "review_state": revision({"sources": record["source_revisions"], "snapshot": record["review_snapshot_revision"]})}
        observed = {"facts": facts, "fingerprint": revision(facts)}
        condition = {"condition": value["condition"], "observed_state": observed}
        compact = {"status": "blocked", "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status="blocked", blocking_condition=condition, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "condition": value["condition"], "next_action": value["next_action"]}
    if status in ("refused", "failed"):
        compact = {"status": status, "outcome": None, "entity": None}
        invocations.update_status(project, invocation_id, status=status, result=compact)
        return {"invocation_id": invocation_id, "result": compact, "next_action": value["next_action"]}
    coordinates = record["coordinates"]
    assay = {
        "schema_version": "2",
        "id": f"C{coordinates['change']}-R{coordinates['round']}-A",
        "change": coordinates["change"],
        "round": coordinates["round"],
        "outcome": value["outcome"],
        "abstract": value["abstract"],
        "findings": value["findings"],
        "invocation_id": invocation_id,
        "reaction_protocol_version": record["protocol_version"],
        "source_revisions": record["source_revisions"],
        "review_snapshot_revision": record["review_snapshot_revision"],
        "actor": {"host": record["host"], "model": record["model"], "reaction": "review"},
    }
    validate_schema("assay", assay)
    _, _, _, compact = specimens.broker_assay(
        project,
        coordinates["slug"],
        expected_revision=resolved["specimen_revision"],
        reason="Brokered the provenance-bound Review readout.",
        assay=assay,
        completion_capability=completion_capability,
    )
    return {"invocation_id": invocation_id, "result": compact}


def map_data(reaction: str | None, map_format: str) -> dict[str, Any]:
    if reaction is not None:
        _reaction(reaction)
    proto = protocol()
    mapping = brief_map()
    counts: dict[str, int] = {}
    for slot in mapping["slots"]:
        counts[slot["source"]] = counts.get(slot["source"], 0) + 1
    missing = []
    for relative in (proto["brief_map"], proto["readout_schema"], proto["entity_schema"]):
        if not (_reaction_root() / relative).resolve().is_file():
            missing.append(relative)
    edges = [{"from": slot["source"], "to": f"brief.{slot['id']}", "selector": slot["selector"], "authority": proto["authority"]["catalyst"]} for slot in mapping["slots"]]
    edges.append({"from": "readout", "to": proto["result_destination"], "selector": "brokered result", "authority": proto["authority"]["broker"]})
    if map_format == "mermaid":
        lines = ["flowchart LR"]
        for index, edge in enumerate(edges, 1):
            lines.append(f"  S{index}[\"{edge['from']}\"] --> D{index}[\"{edge['to']}\"]")
        return {"reaction": "review", "format": "mermaid", "map": "\n".join(lines), "overlap": counts, "missing_consumers": missing}
    return {"reaction": "review", "format": "json", "edges": edges, "overlap": counts, "missing_consumers": missing, "unused_sources": []}


def invoke(project, reaction: str, slug: str | None, *, host: str, model: str | None, change: int | None, round_number: int | None, after: str | None, timeout: float) -> dict[str, Any]:
    selected_model = _select_option(project, host, model)
    inspected = inspect(
        project,
        reaction,
        slug,
        host=host,
        model=selected_model,
        change=change,
        round_number=round_number,
        after=after,
    )
    if inspected["state"] == "complete":
        return {
            "invocation_id": None,
            "result": {"status": "complete", "outcome": inspected["outcome"], "entity": inspected["entity"]},
        }
    if inspected["state"] != "ready":
        raise IsotopeError("agent-not-ready", "Review cannot launch until inspection is ready.", EXIT_REFUSED, inspected)
    descriptor = _load_launch_descriptor(host)
    _preflight_host(project, descriptor, timeout)
    opened = open_invocation(project, reaction, slug, host=host, model=model, change=change, round_number=round_number, after=after)
    if opened["invocation_id"] is None:
        return {"invocation_id": None, "result": opened["result"]}
    invocation_id = opened["invocation_id"]
    capability = opened["completion_capability"]
    schema_path = _reaction_root() / "readout.schema.json"
    transport_schema_path: Path | None = None
    result_path: Path | None = None
    invocation = invocations.read_invocation(project, invocation_id)
    coordinates = invocation["coordinates"]
    prompt = (
        f"Run the explicit Isotope Review agent for invocation {invocation_id}, specimen {coordinates['slug']}, "
        f"change {coordinates['change']}, round {coordinates['round']}. "
        f"Use only scalar coordinates and pull exactly one brief with python .isotope/bin/isotope.py agent brief review --invocation {invocation_id}. "
        "Return only the schema-valid Review readout."
    )
    if host == "codex":
        prompt = "Delegate this task to the isotope_review custom agent and wait for its result. " + prompt
        handle = tempfile.NamedTemporaryFile(prefix="isotope-readout-", suffix=".json", delete=False)
        handle.close()
        result_path = Path(handle.name)
        schema_handle = tempfile.NamedTemporaryFile(prefix="isotope-schema-", suffix=".json", mode="w", encoding="utf-8", delete=False)
        json.dump(_codex_output_schema(), schema_handle, separators=(",", ":"))
        schema_handle.close()
        transport_schema_path = Path(schema_handle.name)
        schema_path = transport_schema_path
    host_schema = {
        key: value
        for key, value in readout_schema().items()
        if key not in ("$schema", "$id")
    }
    replacements = {
        "{readout_schema_json}": json.dumps(host_schema, separators=(",", ":")),
        "{readout_schema_path}": str(schema_path),
        "{result_path}": "" if result_path is None else str(result_path),
        "{project}": str(project.root),
        "{invocation_id}": invocation_id,
        "{prompt}": prompt,
    }
    arguments = []
    for item in descriptor["arguments"]:
        rendered = item
        for marker, value in replacements.items():
            rendered = rendered.replace(marker, value)
        arguments.append(rendered)
    if invocation["model"]:
        model_arguments = [invocation["model"] if item == "{model}" else item for item in descriptor["model_arguments"]]
        arguments[-1:-1] = model_arguments
    child_environment = os.environ.copy()
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    lease = acquire_invocation_lease(project, invocation_id)
    try:
        invocations.update_status(project, invocation_id, status="running")
        try:
            process = _run_host_process(
                [descriptor["executable"], *arguments],
                cwd=project.root,
                env=child_environment,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            recovered = _mark_transport_failed(project, invocation_id)
            if recovered is not None:
                return recovered
            raise IsotopeError(
                "target-timeout",
                "The Review host exceeded the bounded timeout; its process tree was terminated and no assay was brokered.",
                EXIT_CONFLICT,
                {"invocation": invocation_id, "timeout": timeout, "next_action": "launch a fresh catalyst after the timeout condition changes"},
            ) from exc
        except OSError as exc:
            recovered = _mark_transport_failed(project, invocation_id)
            if recovered is not None:
                return recovered
            raise IsotopeError(
                "target-unavailable",
                "The Review host executable became unavailable before launch.",
                EXIT_NOT_FOUND,
                {"host": host, "invocation": invocation_id, "next_action": "repair the installed host CLI and launch a fresh catalyst"},
            ) from exc
        if process.returncode != 0:
            code, message, exit_code, next_action = _transport_failure_code(process.stderr)
            recovered = _mark_transport_failed(project, invocation_id)
            if recovered is not None:
                return recovered
            raise IsotopeError(
                code,
                message,
                exit_code,
                {"host": host, "invocation": invocation_id, "returncode": process.returncode, "next_action": next_action},
            )
        permission_denials: list[Any] = []
        try:
            if descriptor["result_kind"] == "last-message-json":
                raw = result_path.read_text(encoding="utf-8") if result_path is not None else ""
                readout = json.loads(raw)
            else:
                outer = json.loads(process.stdout)
                if not isinstance(outer, dict) or outer.get("is_error") is True:
                    raise ValueError("Claude did not return one successful result envelope")
                permission_denials = outer.get("permission_denials", [])
                if not isinstance(permission_denials, list):
                    raise ValueError("Claude returned an invalid permission_denials field")
                candidates = [key for key in ("structured_output", "result") if key in outer]
                if len(candidates) != 1:
                    raise ValueError("Claude did not return exactly one structured readout candidate")
                if candidates[0] == "result":
                    if outer.get("type") != "result" or outer.get("subtype") != "success" or outer.get("terminal_reason") != "completed":
                        raise ValueError("Claude returned an incomplete result envelope")
                    readout = _decode_host_readout(outer["result"])
                else:
                    readout = outer["structured_output"]
            if not permission_denials:
                readout = _normalize_host_readout(readout)
                _validate_readout(readout)
        except (OSError, ValueError, json.JSONDecodeError, IsotopeError) as exc:
            recovered = _mark_transport_failed(project, invocation_id)
            if recovered is not None:
                return recovered
            raise IsotopeError(
                "result-malformed",
                "The Review host did not return exactly one valid readout; durable state was checked and no assay was brokered.",
                EXIT_MALFORMED,
                {"invocation": invocation_id, "failure_kind": type(exc).__name__, "next_action": "repair the target readout and launch a fresh catalyst"},
            ) from exc
        if permission_denials:
            recovered = _mark_transport_failed(project, invocation_id)
            if recovered is not None:
                return recovered
            raise IsotopeError(
                "authority-unavailable",
                "The Review host attempted a tool outside the non-interactive authority profile.",
                EXIT_REFUSED,
                {"host": host, "invocation": invocation_id, "prompt_attempts": len(permission_denials), "next_action": "repair the declared tool profile and launch a fresh catalyst"},
            )
        return finish(project, reaction, invocation_id, readout, capability)
    finally:
        if result_path is not None:
            result_path.unlink(missing_ok=True)
        if transport_schema_path is not None:
            transport_schema_path.unlink(missing_ok=True)
        release_invocation_lease(lease)
