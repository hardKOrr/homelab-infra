"""Specimen discovery, validation, projections, and audited transactions."""

from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import gitops
from .docs import require_mapped_targets
from .errors import (
    EXIT_AMBIGUOUS,
    EXIT_CONFLICT,
    EXIT_MALFORMED,
    EXIT_NOT_FOUND,
    EXIT_REFUSED,
    EXIT_USAGE,
    IsotopeError,
)
from .journal import JournalWrite, run_transaction, transaction_scope
from .operating import OPERATING_RELATIVE, require_armed, read_operating, advanced_tail
from .paths import CULTURES_DIR, CULTURE_STAGES, ISOTOPE_DIR, Project
from .revisions import canonical_bytes, load_json, revision, write_canonical
from .schemas import HASH_PATTERN, SLUG_PATTERN, validate as validate_schema


SLUG_RE = re.compile(SLUG_PATTERN)
HASH_RE = re.compile(HASH_PATTERN)


@dataclass(frozen=True)
class LocatedSpecimen:
    slug: str
    stage: str
    path: Path
    relative_path: str

    def source(self, specimen_revision: str | None = None) -> dict[str, Any]:
        return {"path": self.relative_path, "revision": specimen_revision}


def _check_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug):
        raise IsotopeError(
            "unsafe-slug",
            "Specimen slugs must contain lowercase letters, digits, and single hyphens only.",
            EXIT_REFUSED,
            {"slug": slug},
        )


def culture_slugs(project: Project) -> dict[str, list[str]]:
    """Slug listing per culture stage; names only, never specimen content."""
    listing: dict[str, list[str]] = {}
    for stage in CULTURE_STAGES:
        directory = project.root / ISOTOPE_DIR / CULTURES_DIR / stage
        listing[stage] = sorted(path.stem for path in directory.glob("*.json")) if directory.is_dir() else []
    return listing


def culture_path(project: Project, stage: str, slug: str) -> Path:
    return project.root / ISOTOPE_DIR / CULTURES_DIR / stage / f"{slug}.json"


def culture_relative(stage: str, slug: str) -> str:
    return f"{ISOTOPE_DIR}/{CULTURES_DIR}/{stage}/{slug}.json"


def locate(project: Project, slug: str) -> LocatedSpecimen:
    _check_slug(slug)
    cultures_root = project.root / ISOTOPE_DIR / CULTURES_DIR
    matches: list[LocatedSpecimen] = []
    for stage in CULTURE_STAGES:
        path = culture_path(project, stage, slug)
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(cultures_root.resolve())
        except ValueError as exc:
            raise IsotopeError(
                "path-escape",
                "The specimen path resolves outside .isotope/cultures.",
                EXIT_REFUSED,
                {"path": path.relative_to(project.root).as_posix()},
            ) from exc
        matches.append(
            LocatedSpecimen(
                slug=slug,
                stage=stage,
                path=resolved,
                relative_path=path.relative_to(project.root).as_posix(),
            )
        )
    if not matches:
        raise IsotopeError(
            "specimen-not-found",
            f"No specimen was found for {slug!r}.",
            EXIT_NOT_FOUND,
            {"slug": slug},
        )
    if len(matches) > 1:
        raise IsotopeError(
            "specimen-ambiguous",
            f"More than one culture stage contains {slug!r}.",
            EXIT_AMBIGUOUS,
            {"slug": slug, "matches": [item.relative_path for item in matches]},
        )
    return matches[0]


def _sequential(records: list[dict], key: str, prefix: str, path: str) -> None:
    for index, record in enumerate(records, 1):
        expected: Any = index if key == "number" else f"{prefix}{index}"
        if record.get(key) != expected:
            raise IsotopeError(
                "schema-invalid",
                f"Records at {path} must use contiguous identities in array order.",
                EXIT_MALFORMED,
                {"path": f"{path}/{index - 1}/{key}", "expected": expected},
            )


def _validate_specimen(value: Any, located: LocatedSpecimen) -> None:
    validate_schema("specimen", value)
    if value["slug"] != located.slug:
        raise IsotopeError(
            "schema-invalid",
            "The specimen slug must match its filename.",
            EXIT_MALFORMED,
            {"path": "/slug", "expected": located.slug},
        )
    _sequential(value["changes"], "number", "", "/changes")
    _sequential(value["decisions"], "id", "D", "/decisions")
    _sequential(value.get("acceptance_criteria", []), "id", "AC", "/acceptance_criteria")
    _sequential(value.get("verification", []), "id", "V", "/verification")
    _sequential(value["acceptances"], "id", "A", "/acceptances")
    _sequential(value["events"], "id", "E", "/events")
    for index, item in enumerate(value["decisions"]):
        provenance = ("trigger", "invocation_id", "reaction_protocol_version", "source_revisions", "actor")
        present = [name for name in provenance if name in item]
        if present and len(present) != len(provenance):
            raise IsotopeError("schema-invalid", "Decision provenance is all-or-none.", EXIT_MALFORMED, {"path": f"/decisions/{index}"})
        if present:
            for name, source_revision in item["source_revisions"].items():
                if not isinstance(source_revision, str) or not HASH_RE.fullmatch(source_revision):
                    raise IsotopeError("schema-invalid", "Decision source revisions must be sha256 hashes.", EXIT_MALFORMED, {"path": f"/decisions/{index}/source_revisions/{name}"})
    for index, item in enumerate(value["acceptances"], 1):
        if item["number"] != index:
            raise IsotopeError("schema-invalid", "Acceptance number must match its identity.", EXIT_MALFORMED, {"path": f"/acceptances/{index - 1}/number"})
        provenance = ("acceptance_snapshot", "invocation_id", "reaction_protocol_version", "source_revisions", "actor")
        present = [name for name in provenance if name in item]
        if present and len(present) != len(provenance):
            raise IsotopeError("schema-invalid", "Acceptance provenance is all-or-none.", EXIT_MALFORMED, {"path": f"/acceptances/{index - 1}"})
        if present:
            for name, source_revision in item["source_revisions"].items():
                if not isinstance(source_revision, str) or not HASH_RE.fullmatch(source_revision):
                    raise IsotopeError("schema-invalid", "Acceptance source revisions must be sha256 hashes.", EXIT_MALFORMED, {"path": f"/acceptances/{index - 1}/source_revisions/{name}"})
    for index, item in enumerate(value["events"], 1):
        if item["order"] != index:
            raise IsotopeError("schema-invalid", "Event order must match its identity.", EXIT_MALFORMED, {"path": f"/events/{index - 1}/order"})

    change_numbers = {item["number"] for item in value["changes"]}
    round_pairs: dict[tuple[int, int], dict] = {}
    round_counts: dict[int, int] = {}
    for index, item in enumerate(value["rounds"]):
        expected_id = f"C{item['change']}-R{item['number']}"
        if item["change"] not in change_numbers or item["id"] != expected_id:
            raise IsotopeError("schema-invalid", "Round identity does not resolve to a change.", EXIT_MALFORMED, {"path": f"/rounds/{index}/id"})
        round_counts[item["change"]] = round_counts.get(item["change"], 0) + 1
        if item["number"] != round_counts[item["change"]]:
            raise IsotopeError("schema-invalid", "Rounds for a change must be contiguous in append order.", EXIT_MALFORMED, {"path": f"/rounds/{index}/number"})
        provenance = ("invocation_id", "reaction_protocol_version", "source_revisions", "actor")
        present = [name for name in provenance if name in item]
        if present and len(present) != len(provenance):
            raise IsotopeError("schema-invalid", "Construction round provenance is all-or-none.", EXIT_MALFORMED, {"path": f"/rounds/{index}"})
        if present:
            for name, source_revision in item["source_revisions"].items():
                if not isinstance(source_revision, str) or not HASH_RE.fullmatch(source_revision):
                    raise IsotopeError("schema-invalid", "Construction source revisions must be sha256 hashes.", EXIT_MALFORMED, {"path": f"/rounds/{index}/source_revisions/{name}"})
        round_pairs[(item["change"], item["number"])] = item
    assay_pairs: set[tuple[int, int]] = set()
    for index, item in enumerate(value["assays"]):
        pair = (item["change"], item["round"])
        if pair not in round_pairs or item["id"] != f"C{item['change']}-R{item['round']}-A":
            raise IsotopeError("schema-invalid", "Assay identity does not resolve to a round.", EXIT_MALFORMED, {"path": f"/assays/{index}/id"})
        if pair in assay_pairs:
            raise IsotopeError("schema-invalid", "Duplicate round assay.", EXIT_MALFORMED, {"path": f"/assays/{index}"})
        if (item["outcome"] == "PASS") == bool(item["findings"]):
            raise IsotopeError("schema-invalid", "PASS assays require no findings; CHANGES requires findings.", EXIT_MALFORMED, {"path": f"/assays/{index}/findings"})
        for name, source_revision in item["source_revisions"].items():
            if not isinstance(source_revision, str) or not HASH_RE.fullmatch(source_revision):
                raise IsotopeError("schema-invalid", "Assay source revisions must be sha256 hashes.", EXIT_MALFORMED, {"path": f"/assays/{index}/source_revisions/{name}"})
        target_round = round_pairs[pair]
        stored = target_round.get("review_snapshot")
        if stored is None or revision(stored) != item["review_snapshot_revision"]:
            raise IsotopeError(
                "schema-invalid",
                "An assay's snapshot revision must match its round's stored Review snapshot.",
                EXIT_MALFORMED,
                {"path": f"/assays/{index}/review_snapshot_revision"},
            )
        assay_pairs.add(pair)

    if value["acceptances"] and value["acceptances"][-1]["verdict"] == "PASS":
        _require_changes_passed(value, error_code="schema-invalid")

    criterion_ids = {item["id"] for item in value.get("acceptance_criteria", [])}
    verification_ids = {item["id"] for item in value.get("verification", [])}
    for index, item in enumerate(value["acceptances"]):
        observed_criteria = [result["criterion_id"] for result in item["criteria"]]
        observed_verification = [result["verification_id"] for result in item["verification"]]
        if len(observed_criteria) != len(set(observed_criteria)) or set(observed_criteria) != criterion_ids:
            raise IsotopeError("schema-invalid", "Acceptance must check every criterion exactly once.", EXIT_MALFORMED, {"path": f"/acceptances/{index}/criteria"})
        if len(observed_verification) != len(set(observed_verification)) or set(observed_verification) != verification_ids:
            raise IsotopeError("schema-invalid", "Acceptance must run every verification exactly once.", EXIT_MALFORMED, {"path": f"/acceptances/{index}/verification"})
        if any(finding["change"] not in change_numbers for finding in item["findings"]):
            raise IsotopeError("schema-invalid", "Acceptance finding references an unknown change.", EXIT_MALFORMED, {"path": f"/acceptances/{index}/findings"})
        has_failure = (
            any(result["status"] == "FAIL" for result in item["criteria"])
            or any(result["status"] == "FAIL" for result in item["verification"])
            or bool(item["findings"])
        )
        if (item["verdict"] == "PASS") == has_failure:
            raise IsotopeError("schema-invalid", "Acceptance verdict does not match its checks and findings.", EXIT_MALFORMED, {"path": f"/acceptances/{index}/verdict"})

    if value.get("outcome") is not None:
        validate_schema("outcome", value["outcome"])
        if not value["acceptances"] or value["acceptances"][-1]["verdict"] != "PASS":
            raise IsotopeError("schema-invalid", "An outcome requires a passing acceptance.", EXIT_MALFORMED, {"path": "/outcome"})
        expression = value["outcome"].get("expression")
        if expression is not None:
            for name, source_revision in expression["source_revisions"].items():
                if not isinstance(source_revision, str) or not HASH_RE.fullmatch(source_revision):
                    raise IsotopeError("schema-invalid", "Expression source revisions must be sha256 hashes.", EXIT_MALFORMED, {"path": f"/outcome/expression/source_revisions/{name}"})

    latest_events: dict[tuple[str, str], tuple[int, dict]] = {}
    for index, event in enumerate(value["events"]):
        if (event["operation"] == "add") != (event["before_hash"] is None):
            raise IsotopeError(
                "schema-invalid",
                "Add events require a null before hash; replace events require a before hash.",
                EXIT_MALFORMED,
                {"path": f"/events/{index}/before_hash"},
            )
        latest_events[(event["entity_kind"], event["entity_id"])] = (index, event)
    for (kind, entity_id), (index, event) in latest_events.items():
        entity = _resolve_entity(value, kind, entity_id)
        if entity is None or revision(entity) != event["after_hash"]:
            raise IsotopeError(
                "schema-invalid",
                "The latest audit event hash does not match its named entity.",
                EXIT_MALFORMED,
                {"path": f"/events/{index}/after_hash", "entity_kind": kind, "entity_id": entity_id},
            )
    if located.stage == "matter":
        forbidden = [name for name in ("rounds", "assays", "acceptances", "events") if value[name]]
        if forbidden or value.get("outcome") is not None:
            raise IsotopeError("schema-invalid", "A matter specimen cannot contain operation or deploy records.", EXIT_MALFORMED, {"path": f"/{forbidden[0]}" if forbidden else "/outcome"})
    elif value.get("demoted_from_revision") is not None:
        raise IsotopeError("schema-invalid", "Demotion provenance is consumed before flux promotion.", EXIT_MALFORMED, {"path": "/demoted_from_revision"})
    if located.stage == "stable":
        # Flux admits Analyze-promoted specimens before Design; completeness is
        # enforced where it is consumed (Acceptance and stability), not at entry.
        for field in ("context", "acceptance_criteria", "changes", "verification"):
            if not value.get(field):
                raise IsotopeError("schema-invalid", f"A {located.stage} specimen requires {field}.", EXIT_MALFORMED, {"path": f"/{field}"})
    if located.stage == "stable":
        if value.get("outcome") is None or not value["acceptances"] or value["acceptances"][-1]["verdict"] != "PASS":
            raise IsotopeError("schema-invalid", "A stable specimen requires a passing acceptance and outcome.", EXIT_MALFORMED, {"path": "/outcome"})


def read_validated(located: LocatedSpecimen) -> tuple[Any, str | None]:
    value, raw = load_json(located.path)
    _validate_specimen(value, located)
    expected = canonical_bytes(value)
    if raw != expected:
        raise IsotopeError(
            "noncanonical",
            "The specimen is valid JSON but is not canonically serialized.",
            EXIT_MALFORMED,
            {"path": located.relative_path, "expected_revision": revision(value)},
        )
    return value, revision(value)


def locate_data(located: LocatedSpecimen) -> dict[str, Any]:
    return {"slug": located.slug, "stage": located.stage, "path": located.relative_path}


def text_projection(located: LocatedSpecimen, value: Any) -> str:
    lines = [f"{value['slug']} [{located.stage}]", f"type: {value['type']}", "", value["goal"]]
    if value.get("context"):
        lines.extend(["", "Context", value["context"]])
    return "\n".join(lines)


# --- Authoring and audited transactions ---------------------------------------------------

CREATE_FIELDS = ("matter", "type", "depends_on", "spec_provenance", "prerequisites", "goal")
OWNED_SET_FIELDS = {
    "intake": {"matter"},
    "analyze": {"type", "depends_on", "spec_provenance", "prerequisites", "goal"},
    "design": {"context", "acceptance_criteria", "changes", "decisions", "verification"},
}
OWNER_SKILL = "operate"


@dataclass(frozen=True)
class _EventSpec:
    operation: str
    entity_kind: str
    entity_id: str
    before: Any
    after: Any


def _invalid_input(path: str, message: str) -> None:
    raise IsotopeError("invalid-input", message, EXIT_MALFORMED, {"path": path})


def _require_payload_keys(payload: Any, keys: set[str]) -> None:
    if not isinstance(payload, dict):
        _invalid_input("/", "The payload must be a JSON object.")
    missing = sorted(keys - set(payload))
    if missing:
        _invalid_input(f"/{missing[0]}", f"Required payload field {missing[0]!r} is missing.")
    extras = sorted(set(payload) - keys)
    if extras:
        _invalid_input(f"/{extras[0]}", f"Unknown payload field {extras[0]!r}.")


def _check_expected(expected: Any, current: str) -> None:
    if not isinstance(expected, str) or not expected:
        _invalid_input("/expected_revision", "expected_revision must be the revision from the caller's most recent read.")
    if expected != current:
        raise IsotopeError(
            "stale-revision",
            "The specimen changed since it was read; re-read and retry with the current revision.",
            EXIT_CONFLICT,
            {"expected": expected, "current": current},
        )


def _matter_location(project: Project, slug: str) -> LocatedSpecimen:
    return LocatedSpecimen(
        slug=slug,
        stage="matter",
        path=culture_path(project, "matter", slug),
        relative_path=culture_relative("matter", slug),
    )


def create(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    with transaction_scope(project):
        return _create_locked(project, slug, payload)


def _create_locked(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    _check_slug(slug)
    _require_payload_keys(payload, set(CREATE_FIELDS))
    exists = True
    try:
        locate(project, slug)
    except IsotopeError as exc:
        if exc.code == "specimen-not-found":
            exists = False
        elif exc.code != "specimen-ambiguous":
            raise
    if exists:
        raise IsotopeError("specimen-exists", f"A specimen already exists for {slug!r}.", EXIT_CONFLICT, {"slug": slug})
    value = {
        "schema_version": "2",
        "slug": slug,
        **{field: payload[field] for field in CREATE_FIELDS},
        "changes": [],
        "decisions": [],
        "rounds": [],
        "assays": [],
        "acceptances": [],
        "events": [],
    }
    located = _matter_location(project, slug)
    _validate_specimen(value, located)
    new_revision = revision(value)
    run_transaction(
        project,
        journal_type="specimen",
        operation="specimen.create",
        writes=[JournalWrite(located.relative_path, None, new_revision, value)],
    )
    return located, new_revision


def set_fields(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    with transaction_scope(project):
        return _set_fields_locked(project, slug, payload)


def _set_fields_locked(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    _require_payload_keys(payload, {"reaction", "expected_revision", "fields"})
    reaction = payload["reaction"]
    if reaction not in OWNED_SET_FIELDS:
        _invalid_input("/reaction", "reaction must be 'intake', 'analyze', or 'design'.")
    fields = payload["fields"]
    if not isinstance(fields, dict) or not fields:
        _invalid_input("/fields", "fields must be a non-empty object.")
    unowned = sorted(set(fields) - OWNED_SET_FIELDS[reaction])
    if unowned:
        raise IsotopeError(
            "field-not-owned",
            f"{reaction} does not own the field {unowned[0]!r}.",
            EXIT_REFUSED,
            {"reaction": reaction, "field": unowned[0], "owned": sorted(OWNED_SET_FIELDS[reaction])},
        )
    located = locate(project, slug)
    if located.stage != "matter":
        raise IsotopeError(
            "wrong-stage",
            "Fields are set while the specimen is in matter/; an armed operation mutates only through audited transactions.",
            EXIT_REFUSED,
            {"slug": slug, "stage": located.stage},
        )
    value, current = read_validated(located)
    _check_expected(payload["expected_revision"], current)
    value.update(deepcopy(fields))
    _validate_specimen(value, located)
    new_revision = revision(value)
    run_transaction(
        project,
        journal_type="specimen",
        operation="specimen.set",
        writes=[JournalWrite(located.relative_path, current, new_revision, value)],
    )
    return located, new_revision


def promote(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    """Journaled culture move matter -> flux; Analyze's promotion freezes /matter."""
    with transaction_scope(project):
        return _promote_locked(project, slug, payload)


def _promote_locked(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    _require_payload_keys(payload, {"expected_revision"})
    located = locate(project, slug)
    if located.stage != "matter":
        raise IsotopeError(
            "wrong-stage",
            "Promotion moves a matter specimen into flux; this specimen is already past matter.",
            EXIT_REFUSED,
            {"slug": slug, "stage": located.stage},
        )
    value, current = read_validated(located)
    _check_expected(payload["expected_revision"], current)
    destination = LocatedSpecimen(
        slug=slug,
        stage="flux",
        path=culture_path(project, "flux", slug),
        relative_path=culture_relative("flux", slug),
    )
    _validate_specimen(value, destination)
    run_transaction(
        project,
        journal_type="specimen",
        operation="specimen.promote",
        writes=[
            JournalWrite(destination.relative_path, None, current, value),
            JournalWrite(located.relative_path, current, None),
        ],
    )
    return destination, current


def demote(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    """Explicitly return pre-construction flux to matter for Intake-owned core rework."""
    _require_payload_keys(payload, {"expected_revision"})
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage == "matter":
            value, current = read_validated(located)
            if payload["expected_revision"] not in (current, value.get("demoted_from_revision")):
                _check_expected(payload["expected_revision"], current)
            return located, current
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Demotion moves a flux specimen back to matter.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        if read_operating(project) is not None:
            raise IsotopeError("lifecycle-refused", "Demotion requires no operating binding.", EXIT_REFUSED, {"next_action": "tear down the operation first"})
        value, current = read_validated(located)
        _check_expected(payload["expected_revision"], current)
        occupied = []
        for field in ("context", "acceptance_criteria", "changes", "verification", "decisions", "rounds", "assays", "acceptances"):
            if value.get(field):
                occupied.append(field)
        if value.get("outcome") is not None:
            occupied.append("outcome")
        non_analysis_events = [event["id"] for event in value["events"] if event.get("reaction") != "analyze"]
        if occupied or non_analysis_events:
            raise IsotopeError(
                "demotion-refused",
                "Core demotion is available only before Design or later operational history.",
                EXIT_REFUSED,
                {"occupied": occupied, "events": non_analysis_events},
            )
        demoted = deepcopy(value)
        for field in ("context", "acceptance_criteria", "verification", "outcome"):
            demoted.pop(field, None)
        demoted["changes"] = []
        demoted["decisions"] = []
        demoted["rounds"] = []
        demoted["assays"] = []
        demoted["acceptances"] = []
        demoted["events"] = []
        demoted["demoted_from_revision"] = current
        destination = LocatedSpecimen(slug, "matter", culture_path(project, "matter", slug), culture_relative("matter", slug))
        _validate_specimen(demoted, destination)
        new_revision = revision(demoted)
        run_transaction(
            project,
            journal_type="specimen",
            operation="specimen.demote",
            writes=[
                JournalWrite(destination.relative_path, None, new_revision, demoted),
                JournalWrite(located.relative_path, current, None),
            ],
        )
        return destination, new_revision


def require_deployable(value: dict[str, Any]) -> None:
    acceptance = value["acceptances"][-1] if value["acceptances"] else None
    if acceptance is None or acceptance["verdict"] != "PASS":
        raise IsotopeError("deploy-refused", "Deploy requires the latest whole-specimen Acceptance to pass.", EXIT_REFUSED)
    outcome = value.get("outcome")
    if outcome is None or outcome.get("expression") is None:
        raise IsotopeError("deploy-refused", "Deploy requires an outcome with completed Expression evidence.", EXIT_REFUSED)


def stabilize(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str]:
    """Journal the deploy-ready flux-to-stable culture move while retaining operation recovery facts."""
    _require_payload_keys(payload, {"expected_revision"})
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage == "stable":
            value, current = read_validated(located)
            _check_expected(payload["expected_revision"], current)
            require_deployable(value)
            return located, current
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Stable promotion requires a flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        _check_expected(payload["expected_revision"], current)
        require_armed(project, slug, current)
        require_deployable(value)
        destination = LocatedSpecimen(slug, "stable", culture_path(project, "stable", slug), culture_relative("stable", slug))
        _validate_specimen(value, destination)
        run_transaction(
            project,
            journal_type="specimen",
            operation="specimen.stabilize",
            writes=[
                JournalWrite(destination.relative_path, None, current, value),
                JournalWrite(located.relative_path, current, None),
            ],
        )
        return destination, current


def _flux_transaction(
    project: Project,
    slug: str,
    payload: Any,
    reaction: str,
    operation: str,
    apply: Callable[[dict, Any], _EventSpec],
) -> tuple[LocatedSpecimen, str, dict]:
    with transaction_scope(project):
        return _flux_transaction_locked(project, slug, payload, reaction, operation, apply)


def _flux_transaction_locked(
    project: Project,
    slug: str,
    payload: Any,
    reaction: str,
    operation: str,
    apply: Callable[[dict, Any], _EventSpec],
) -> tuple[LocatedSpecimen, str, dict]:
    _require_payload_keys(payload, {"expected_revision", "reason", "entity"})
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason:
        _invalid_input("/reason", "reason must be a non-empty sentence.")
    located = locate(project, slug)
    if located.stage != "flux":
        raise IsotopeError(
            "wrong-stage",
            "This transaction drives an armed operation, so the specimen must be in flux/.",
            EXIT_REFUSED,
            {"slug": slug, "stage": located.stage},
        )
    value, current = read_validated(located)
    operating = require_armed(project, slug, current)
    _check_expected(payload["expected_revision"], current)
    spec = apply(value, payload["entity"])
    event = {
        "schema_version": "2",
        "id": f"E{len(value['events']) + 1}",
        "order": len(value["events"]) + 1,
        "operation": spec.operation,
        "entity_kind": spec.entity_kind,
        "entity_id": spec.entity_id,
        "reason": reason,
        "reaction": reaction,
        "owner": OWNER_SKILL,
        "before_hash": None if spec.before is None else revision(spec.before),
        "after_hash": revision(spec.after),
        "prior_revision": current,
    }
    value["events"].append(event)
    _validate_specimen(value, located)
    new_revision = revision(value)
    tail = advanced_tail(operating, new_revision)
    run_transaction(
        project,
        journal_type="specimen",
        operation=operation,
        writes=[
            JournalWrite(located.relative_path, current, new_revision, value),
            JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
        ],
    )
    return located, new_revision, event


def _append_entity(entity_kind: str, collection: str) -> Callable[[dict, Any], _EventSpec]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema(entity_kind, entity)
        value[collection].append(entity)
        return _EventSpec("add", entity_kind, str(entity["id"]), None, entity)

    return apply


def _latest_assay(value: dict, change: int) -> dict | None:
    rounds = [item for item in value["rounds"] if item["change"] == change]
    if not rounds:
        return None
    number = max(item["number"] for item in rounds)
    return next(
        (item for item in value["assays"] if item["change"] == change and item["round"] == number),
        None,
    )


def _require_changes_passed(value: dict, *, error_code: str = "lifecycle-refused") -> None:
    if not value["changes"]:
        raise IsotopeError(
            error_code,
            "Acceptance requires at least one planned change.",
            EXIT_MALFORMED if error_code == "schema-invalid" else EXIT_REFUSED,
            {"path": "/changes"},
        )
    for change in value["changes"]:
        assay = _latest_assay(value, change["number"])
        if assay is None or assay["outcome"] != "PASS":
            raise IsotopeError(
                error_code,
                "Every planned change must have a latest PASS assay before acceptance.",
                EXIT_MALFORMED if error_code == "schema-invalid" else EXIT_REFUSED,
                {"path": "/acceptances", "change": change["number"]},
            )


def _live_snapshot(project: Project, operating: dict) -> tuple[dict, str]:
    return gitops.review_snapshot(project, operating["base_commit"])


def round_append(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        if isinstance(entity, dict) and "review_snapshot" in entity:
            _invalid_input(
                "/entity/review_snapshot",
                "The Review snapshot is captured by the CLI on a completed round, never supplied.",
            )
        validate_schema("round", entity)
        prior_rounds = [item for item in value["rounds"] if item["change"] == entity["change"]]
        if prior_rounds and prior_rounds[-1]["status"] == "complete":
            prior = _latest_assay(value, entity["change"])
            repair_allowed = bool(
                prior
                and prior["outcome"] == "PASS"
                and value["acceptances"]
                and value["acceptances"][-1]["verdict"] == "CHANGES"
                and any(
                    finding["change"] == entity["change"]
                    for finding in value["acceptances"][-1]["findings"]
                )
            )
            if prior is None or (prior["outcome"] != "CHANGES" and not repair_allowed):
                raise IsotopeError(
                    "lifecycle-refused",
                    "A new round requires the prior round's CHANGES assay or a matching acceptance finding.",
                    EXIT_REFUSED,
                    {"change": entity["change"]},
                )
        if entity["status"] == "complete":
            operating = require_armed(project, slug, payload["expected_revision"])
            manifest, _ = _live_snapshot(project, operating)
            entity = deepcopy(entity)
            entity["review_snapshot"] = manifest
        value["rounds"].append(entity)
        return _EventSpec("add", "round", str(entity["id"]), None, entity)

    return _flux_transaction(project, slug, payload, "construction", "specimen.round.append", apply)


def assay_append(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    _require_payload_keys(
        payload,
        {"expected_revision", "reason", "entity", "completion_capability"},
    )
    completion_capability = payload["completion_capability"]
    if not isinstance(completion_capability, str) or not completion_capability:
        _invalid_input(
            "/completion_capability",
            "completion_capability must be the wrapper-retained one-use value.",
        )
    transaction_payload = {
        key: payload[key] for key in ("expected_revision", "reason", "entity")
    }

    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema("assay", entity)
        target = next(
            (
                item
                for item in value["rounds"]
                if item["change"] == entity["change"] and item["number"] == entity["round"]
            ),
            None,
        )
        if target is None:
            raise IsotopeError(
                "round-not-found",
                "The assay does not resolve to a recorded round.",
                EXIT_NOT_FOUND,
                {"change": entity["change"], "round": entity["round"]},
            )
        stored = target.get("review_snapshot")
        if stored is None:
            raise IsotopeError(
                "lifecycle-refused",
                "Review judges a completed round carrying its captured snapshot.",
                EXIT_REFUSED,
                {"change": entity["change"], "round": entity["round"]},
            )
        specimen_source = entity["source_revisions"].get("specimen")
        if specimen_source != payload["expected_revision"]:
            raise IsotopeError(
                "stale-review-source",
                "The assay's specimen source revision does not match the specimen being written.",
                EXIT_CONFLICT,
                {"source": specimen_source, "specimen": payload["expected_revision"]},
            )
        operating = require_armed(project, slug, payload["expected_revision"])
        _, live_revision = _live_snapshot(project, operating)
        stored_revision = revision(stored)
        if live_revision != stored_revision or entity["review_snapshot_revision"] != stored_revision:
            raise IsotopeError(
                "stale-review-source",
                "A Git-visible Review input changed since the round's snapshot was captured.",
                EXIT_CONFLICT,
                {
                    "stored": stored_revision,
                    "live": live_revision,
                    "claimed": entity["review_snapshot_revision"],
                },
            )
        _require_invocation(project, entity, slug, completion_capability)
        value["assays"].append(entity)
        return _EventSpec("add", "assay", str(entity["id"]), None, entity)

    return _flux_transaction(
        project,
        slug,
        transaction_payload,
        "review",
        "specimen.assay.append",
        apply,
    )


def broker_assay(
    project: Project,
    slug: str,
    *,
    expected_revision: str,
    reason: str,
    assay: dict[str, Any],
    completion_capability: str,
) -> tuple[LocatedSpecimen, str, dict, dict]:
    """Commit assay, operating tail, and compact invocation result atomically."""
    from .invocations import invocation_relative, read_invocation

    if not isinstance(reason, str) or not reason:
        _invalid_input("/reason", "reason must be a non-empty sentence.")
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Review brokers only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        operating = require_armed(project, slug, current)
        _check_expected(expected_revision, current)
        invocation = read_invocation(project, assay["invocation_id"])
        existing = next(
            (item for item in value["assays"] if item["change"] == assay["change"] and item["round"] == assay["round"]),
            None,
        )
        if existing is not None:
            if canonical_bytes(existing) == canonical_bytes(assay) and invocation["status"] == "complete":
                return located, current, value["events"][-1], invocation["result"]
            raise IsotopeError(
                "assay-race",
                "A different invocation already owns this Review destination.",
                EXIT_CONFLICT,
                {"existing_invocation": existing["invocation_id"], "invocation": assay["invocation_id"]},
            )
        validate_schema("assay", assay)
        target = next(
            (item for item in value["rounds"] if item["change"] == assay["change"] and item["number"] == assay["round"]),
            None,
        )
        if target is None or target.get("review_snapshot") is None:
            raise IsotopeError("round-not-found", "Review requires the matching completed round and snapshot.", EXIT_NOT_FOUND, {"change": assay["change"], "round": assay["round"]})
        if assay["source_revisions"].get("specimen") != current:
            raise IsotopeError("stale-review-source", "The specimen changed after briefing.", EXIT_CONFLICT, {"source": assay["source_revisions"].get("specimen"), "specimen": current})
        _, live_snapshot_revision = _live_snapshot(project, operating)
        stored_snapshot_revision = revision(target["review_snapshot"])
        if live_snapshot_revision != stored_snapshot_revision or assay["review_snapshot_revision"] != stored_snapshot_revision:
            raise IsotopeError("stale-review-source", "A Git-visible Review input changed after briefing.", EXIT_CONFLICT, {"stored": stored_snapshot_revision, "live": live_snapshot_revision, "claimed": assay["review_snapshot_revision"]})
        _require_invocation(project, assay, slug, completion_capability)
        value["assays"].append(assay)
        event = {
            "schema_version": "2",
            "id": f"E{len(value['events']) + 1}",
            "order": len(value["events"]) + 1,
            "operation": "add",
            "entity_kind": "assay",
            "entity_id": assay["id"],
            "reason": reason,
            "reaction": "review",
            "owner": OWNER_SKILL,
            "before_hash": None,
            "after_hash": revision(assay),
            "prior_revision": current,
        }
        value["events"].append(event)
        _validate_specimen(value, located)
        new_revision = revision(value)
        tail = advanced_tail(operating, new_revision)
        result = {
            "status": "complete",
            "outcome": assay["outcome"],
            "entity": {"kind": "assay", "id": assay["id"], "revision": revision(assay)},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="review.brokered-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, value),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation["id"]), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return located, new_revision, event, result


def record_construction(
    project: Project,
    slug: str,
    *,
    expected_revision: str,
    reason: str,
    entity: dict[str, Any],
    source_guard: Callable[[], None],
) -> tuple[LocatedSpecimen, str, dict, dict]:
    """Commit one native Construction round, operating tail, and compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    if not isinstance(reason, str) or not reason:
        _invalid_input("/reason", "reason must be a non-empty sentence.")
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Construction records only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        invocation = read_invocation(project, entity.get("invocation_id", ""))
        existing = next(
            (item for item in value["rounds"] if item["change"] == entity.get("change") and item["number"] == entity.get("number")),
            None,
        )
        if existing is not None:
            comparable = {key: item for key, item in existing.items() if key != "review_snapshot"}
            if canonical_bytes(comparable) == canonical_bytes(entity) and invocation["result"] is not None:
                return located, current, value["events"][-1], invocation["result"]
            raise IsotopeError("round-race", "A different result already owns this Construction destination.", EXIT_CONFLICT, {"round": entity.get("number")})
        operating = require_armed(project, slug, current)
        _check_expected(expected_revision, current)
        source_guard()
        validate_schema("round", entity)
        coordinates = invocation["coordinates"]
        mismatches = []
        if invocation["reaction"] != "construction":
            mismatches.append("reaction")
        if coordinates.get("slug") != slug or coordinates.get("change") != entity["change"] or coordinates.get("round") != entity["number"]:
            mismatches.append("coordinates")
        if invocation["protocol_version"] != entity.get("reaction_protocol_version"):
            mismatches.append("protocol_version")
        if invocation["source_revisions"] != entity.get("source_revisions"):
            mismatches.append("source_revisions")
        if entity.get("source_revisions", {}).get("specimen") != current:
            mismatches.append("specimen_revision")
        actor = entity.get("actor", {})
        if actor.get("host") != invocation["host"] or actor.get("model") != invocation["model"] or actor.get("reaction") != "construction":
            mismatches.append("actor")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if not {"workspace.files.write", "specimen.round.append"}.issubset(invocation["allowed_effects"]):
            mismatches.append("allowed_effects")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Construction round provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation["id"], "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Construction invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation["id"], "status": invocation["status"]})
        prior_rounds = [item for item in value["rounds"] if item["change"] == entity["change"]]
        if entity["number"] != len(prior_rounds) + 1:
            raise IsotopeError("round-race", "Construction rounds must be contiguous for one change.", EXIT_CONFLICT, {"expected": len(prior_rounds) + 1, "actual": entity["number"]})
        if prior_rounds and prior_rounds[-1]["status"] == "complete":
            prior_assay = _latest_assay(value, entity["change"])
            repair_allowed = bool(
                prior_assay
                and prior_assay["outcome"] == "PASS"
                and value["acceptances"]
                and value["acceptances"][-1]["verdict"] == "CHANGES"
                and any(finding["change"] == entity["change"] for finding in value["acceptances"][-1]["findings"])
            )
            if prior_assay is None or (prior_assay["outcome"] != "CHANGES" and not repair_allowed):
                raise IsotopeError("lifecycle-refused", "A later Construction round requires causal Review or Acceptance findings.", EXIT_REFUSED, {"change": entity["change"]})
        observed_paths = gitops.changed_paths(project)
        if sorted(set(entity["files_touched"])) != observed_paths:
            raise IsotopeError("construction-scope-mismatch", "files_touched must exactly match the Git-visible Construction worktree.", EXIT_CONFLICT, {"declared": sorted(set(entity["files_touched"])), "observed": observed_paths})
        recorded = deepcopy(entity)
        if recorded["status"] == "complete":
            snapshot, _ = _live_snapshot(project, operating)
            recorded["review_snapshot"] = snapshot
        value["rounds"].append(recorded)
        event = {
            "schema_version": "2",
            "id": f"E{len(value['events']) + 1}",
            "order": len(value["events"]) + 1,
            "operation": "add",
            "entity_kind": "round",
            "entity_id": recorded["id"],
            "reason": reason,
            "reaction": "construction",
            "owner": OWNER_SKILL,
            "before_hash": None,
            "after_hash": revision(recorded),
            "prior_revision": current,
        }
        value["events"].append(event)
        _validate_specimen(value, located)
        new_revision = revision(value)
        tail = advanced_tail(operating, new_revision)
        invocation_status = {"complete": "complete", "decision-needed": "needs-user", "blocked": "blocked"}[recorded["status"]]
        result = {
            "status": invocation_status,
            "outcome": recorded["status"],
            "entity": {"kind": "round", "id": recorded["id"], "revision": revision(recorded)},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = invocation_status
        updated_invocation["result"] = result
        if invocation_status == "needs-user":
            updated_invocation["questions"] = [{"id": f"Q{index}", "text": text, "answer": None} for index, text in enumerate(recorded.get("decision_questions", []), 1)]
        if invocation_status == "blocked":
            _, workspace_revision = _live_snapshot(project, operating)
            facts = {
                "construction_state": revision({key: value for key, value in invocation["source_revisions"].items() if key not in ("specimen", "operating")}),
                "workspace": workspace_revision,
                "blockers": recorded.get("blockers", []),
            }
            updated_invocation["blocking_condition"] = {
                "condition": recorded["blockers"][0],
                "observed_state": {"facts": facts, "fingerprint": revision(facts)},
            }
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="construction.native-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, value),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation["id"]), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return located, new_revision, event, result


def record_decision(
    project: Project,
    slug: str,
    *,
    expected_revision: str,
    reason: str,
    entity: dict[str, Any],
    source_guard: Callable[[], None],
) -> tuple[LocatedSpecimen, str, dict, dict]:
    """Commit one native Decision effect, operating tail, and compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    if not isinstance(reason, str) or not reason:
        _invalid_input("/reason", "reason must be a non-empty sentence.")
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Decision records only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        invocation = read_invocation(project, entity.get("invocation_id", ""))
        mode = invocation["coordinates"].get("mode")
        target = next((item for item in value["decisions"] if item["id"] == entity.get("id")), None)
        if invocation["status"] == "complete" and invocation["result"] is not None:
            if target == entity:
                return located, current, value["events"][-1], invocation["result"]
            raise IsotopeError("decision-race", "The invocation already recorded a different Decision result.", EXIT_CONFLICT, {"decision": entity.get("id")})
        operating = require_armed(project, slug, current)
        _check_expected(expected_revision, current)
        source_guard()
        validate_schema("decision", entity)
        coordinates = invocation["coordinates"]
        mismatches = []
        if invocation["reaction"] != "decision":
            mismatches.append("reaction")
        if coordinates.get("slug") != slug or coordinates.get("decision") != entity["id"]:
            mismatches.append("coordinates")
        if entity.get("trigger") != {"invocation_id": coordinates.get("question_invocation"), "question_id": coordinates.get("question")}:
            mismatches.append("trigger")
        if invocation["protocol_version"] != entity.get("reaction_protocol_version"):
            mismatches.append("protocol_version")
        if invocation["source_revisions"] != entity.get("source_revisions"):
            mismatches.append("source_revisions")
        if entity.get("source_revisions", {}).get("specimen") != current:
            mismatches.append("specimen_revision")
        actor = entity.get("actor", {})
        if actor.get("host") != invocation["host"] or actor.get("model") != invocation["model"] or actor.get("reaction") != "decision":
            mismatches.append("actor")
        expected_effect = f"specimen.decision.{mode}"
        if mode not in ("add", "supersede") or expected_effect not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Decision provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation["id"], "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Decision invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation["id"], "status": invocation["status"]})
        before = None
        operation = "add"
        if mode == "add":
            expected_id = f"D{len(value['decisions']) + 1}"
            if target is not None or entity["id"] != expected_id:
                raise IsotopeError("decision-race", "A different result owns the Decision add destination.", EXIT_CONFLICT, {"expected": expected_id, "actual": entity["id"]})
            value["decisions"].append(entity)
            outcome = "added"
        else:
            if target is None:
                raise IsotopeError("decision-not-found", "Decision supersede requires an existing identity.", EXIT_NOT_FOUND, {"decision": entity["id"]})
            if entity["question"] != target["question"]:
                raise IsotopeError("decision-question-mismatch", "A superseded decision preserves its original question.", EXIT_CONFLICT, {"decision": entity["id"]})
            before = deepcopy(target)
            value["decisions"][value["decisions"].index(target)] = entity
            operation = "replace"
            outcome = "superseded"
        event = {
            "schema_version": "2",
            "id": f"E{len(value['events']) + 1}",
            "order": len(value["events"]) + 1,
            "operation": operation,
            "entity_kind": "decision",
            "entity_id": entity["id"],
            "reason": reason,
            "reaction": "decision",
            "owner": OWNER_SKILL,
            "before_hash": None if before is None else revision(before),
            "after_hash": revision(entity),
            "prior_revision": current,
        }
        value["events"].append(event)
        _validate_specimen(value, located)
        new_revision = revision(value)
        tail = advanced_tail(operating, new_revision)
        result = {
            "status": "complete",
            "outcome": outcome,
            "entity": {"kind": "decision", "id": entity["id"], "revision": revision(entity)},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="decision.native-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, value),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation["id"]), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return located, new_revision, event, result


ANALYSIS_FIELDS = ("type", "depends_on", "spec_provenance", "prerequisites", "goal")
DESIGN_FIELDS = ("context", "acceptance_criteria", "changes", "verification")


def _analysis_entity(value: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(value[field]) for field in ANALYSIS_FIELDS}


def _design_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(value[field]) for field in DESIGN_FIELDS}


def _audit_event(
    value: dict[str, Any],
    *,
    operation: str,
    entity_kind: str,
    entity_id: str,
    reason: str,
    reaction: str,
    prior_revision: str,
    before: Any,
    after: Any,
) -> dict[str, Any]:
    event = {
        "schema_version": "2",
        "id": f"E{len(value['events']) + 1}",
        "order": len(value["events"]) + 1,
        "operation": operation,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "reason": reason,
        "reaction": reaction,
        "owner": OWNER_SKILL,
        "before_hash": None if before is None else revision(before),
        "after_hash": revision(after),
        "prior_revision": prior_revision,
    }
    value["events"].append(event)
    return event


def record_expression(
    project: Project,
    invocation_id: str,
    *,
    entity: dict[str, Any],
    source_guard: Callable[[], None],
) -> dict[str, Any]:
    """Commit the outcome expression evidence, operating tail, and compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    with transaction_scope(project):
        invocation = read_invocation(project, invocation_id)
        slug = invocation["coordinates"].get("slug")
        if invocation["status"] == "complete" and invocation["result"] is not None:
            stored = invocation["result"]
            matching = stored.get("status") == "complete"
            existing = None
            try:
                located = locate(project, slug)
                value, _ = read_validated(located)
                existing = (value.get("outcome") or {}).get("expression")
            except IsotopeError:
                matching = False
            if matching and existing == entity:
                return stored
            raise IsotopeError("expression-race", "The invocation already recorded a different Expression result.", EXIT_CONFLICT, {"invocation": invocation_id})
        mismatches = []
        if invocation["reaction"] != "expression":
            mismatches.append("reaction")
        if "specimen.outcome.express" not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if entity.get("invocation_id") != invocation_id or entity.get("reaction_protocol_version") != invocation["protocol_version"] or entity.get("source_revisions") != invocation["source_revisions"]:
            mismatches.append("provenance")
        actor = entity.get("actor", {})
        if actor.get("host") != invocation["host"] or actor.get("model") != invocation["model"] or actor.get("reaction") != "expression":
            mismatches.append("actor")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Expression provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation_id, "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Expression invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation_id, "status": invocation["status"]})
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Expression records only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        operating = require_armed(project, slug, current)
        source_guard()
        validate_schema("expression", entity)
        outcome = value.get("outcome")
        if outcome is None:
            raise IsotopeError("outcome-missing", "Expression requires the specimen's outcome record.", EXIT_REFUSED, {"slug": slug})
        if outcome.get("expression") is not None:
            raise IsotopeError("expression-race", "A different result already occupies the expression destination.", EXIT_CONFLICT, {"slug": slug})
        before = deepcopy(outcome)
        updated_outcome = {**outcome, "expression": deepcopy(entity)}
        value["outcome"] = updated_outcome
        event = {
            "schema_version": "2",
            "id": f"E{len(value['events']) + 1}",
            "order": len(value["events"]) + 1,
            "operation": "replace",
            "entity_kind": "outcome",
            "entity_id": "outcome",
            "reason": "Expressed the outcome into its declared documentation targets.",
            "reaction": "expression",
            "owner": OWNER_SKILL,
            "before_hash": revision(before),
            "after_hash": revision(updated_outcome),
            "prior_revision": current,
        }
        value["events"].append(event)
        _validate_specimen(value, located)
        new_revision = revision(value)
        tail = advanced_tail(operating, new_revision)
        result = {
            "status": "complete",
            "outcome": "expressed",
            "entity": {
                "kind": "outcome",
                "id": "outcome",
                "revision": revision(updated_outcome),
                "targets": [f"{target['path']}#{target['section_id']}" for target in entity["targets"]],
            },
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="expression.native-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, value),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation_id), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return result


def record_design(
    project: Project,
    invocation_id: str,
    *,
    entity: dict[str, Any],
    source_guard: Callable[[], None],
) -> dict[str, Any]:
    """Commit the whole vacant design, the operating tail, and the compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    with transaction_scope(project):
        invocation = read_invocation(project, invocation_id)
        slug = invocation["coordinates"].get("slug")
        if invocation["status"] == "complete" and invocation["result"] is not None:
            stored = invocation["result"]
            matching = stored.get("status") == "complete"
            try:
                located = locate(project, slug)
                value, _ = read_validated(located)
            except IsotopeError:
                matching = False
            result_entity = stored.get("entity") or {}
            if (
                matching
                and result_entity.get("kind") == "specimen"
                and result_entity.get("id") == slug
                and _design_projection(value) == entity
            ):
                return stored
            raise IsotopeError("design-race", "The invocation already recorded a different Design result.", EXIT_CONFLICT, {"invocation": invocation_id})
        mismatches = []
        if invocation["reaction"] != "design":
            mismatches.append("reaction")
        if "specimen.design.write" not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Design provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation_id, "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Design invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation_id, "status": invocation["status"]})
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Design records only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        operating = require_armed(project, slug, current)
        source_guard()
        validate_schema("design", entity)
        occupied = [field for field in DESIGN_FIELDS if value.get(field)]
        if occupied:
            raise IsotopeError("design-race", "A different result already occupies the design destination.", EXIT_CONFLICT, {"slug": slug, "fields": occupied})
        updated = {**value, **deepcopy(entity)}
        _audit_event(
            updated,
            operation="add",
            entity_kind="design-context",
            entity_id="context",
            reason="Recorded the Design context.",
            reaction="design",
            prior_revision=current,
            before=None,
            after={"context": updated["context"]},
        )
        for criterion in updated["acceptance_criteria"]:
            _audit_event(
                updated,
                operation="add",
                entity_kind="acceptance-criterion",
                entity_id=criterion["id"],
                reason="Recorded a Design acceptance criterion.",
                reaction="design",
                prior_revision=current,
                before=None,
                after=criterion,
            )
        for change in updated["changes"]:
            _audit_event(
                updated,
                operation="add",
                entity_kind="change",
                entity_id=str(change["number"]),
                reason="Recorded a Design change.",
                reaction="design",
                prior_revision=current,
                before=None,
                after=change,
            )
        for verification in updated["verification"]:
            _audit_event(
                updated,
                operation="add",
                entity_kind="verification",
                entity_id=verification["id"],
                reason="Recorded a Design verification step.",
                reaction="design",
                prior_revision=current,
                before=None,
                after=verification,
            )
        _validate_specimen(updated, located)
        new_revision = revision(updated)
        tail = advanced_tail(operating, new_revision)
        result = {
            "status": "complete",
            "outcome": "designed",
            "entity": {"kind": "specimen", "id": slug, "revision": new_revision},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="design.native-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, updated),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation_id), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return result


def record_analyze(
    project: Project,
    invocation_id: str,
    *,
    fields: dict[str, Any],
    source_guard: Callable[[], None],
) -> dict[str, Any]:
    """Commit the Analyze fields, the matter->flux promotion, and the compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    with transaction_scope(project):
        invocation = read_invocation(project, invocation_id)
        slug = invocation["coordinates"].get("slug")
        if invocation["status"] == "complete" and invocation["result"] is not None:
            stored = invocation["result"]
            matching = stored.get("status") == "complete"
            try:
                located = locate(project, slug)
                value, _ = read_validated(located)
            except IsotopeError:
                matching = False
            entity = stored.get("entity") or {}
            if (
                matching
                and entity.get("kind") == "specimen"
                and entity.get("id") == slug
                and _analysis_entity(value) == fields
            ):
                return stored
            raise IsotopeError("analyze-race", "The invocation already recorded a different Analyze result.", EXIT_CONFLICT, {"invocation": invocation_id})
        mismatches = []
        if invocation["reaction"] != "analyze":
            mismatches.append("reaction")
        if "specimen.analyze.promote" not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Analyze provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation_id, "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Analyze invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation_id, "status": invocation["status"]})
        source_guard()
        validate_schema("analysis", fields)
        known = {name for slugs in culture_slugs(project).values() for name in slugs}
        unknown = sorted(set(fields["depends_on"]) - (known - {slug}))
        if unknown:
            raise IsotopeError(
                "dependency-not-found",
                "Analyze dependencies must name other existing specimens.",
                EXIT_REFUSED,
                {"slug": slug, "depends_on": unknown},
            )
        located = locate(project, slug)
        if located.stage != "matter":
            raise IsotopeError("wrong-stage", "Analyze structures and promotes a matter specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        updated = {**value, **deepcopy(fields)}
        updated.pop("demoted_from_revision", None)
        destination = LocatedSpecimen(
            slug=slug,
            stage="flux",
            path=culture_path(project, "flux", slug),
            relative_path=culture_relative("flux", slug),
        )
        _audit_event(
            updated,
            operation="add",
            entity_kind="analysis",
            entity_id="analysis",
            reason="Recorded the Analyze structure and flux promotion.",
            reaction="analyze",
            prior_revision=current,
            before=None,
            after=_analysis_entity(updated),
        )
        _validate_specimen(updated, destination)
        new_revision = revision(updated)
        result = {
            "status": "complete",
            "outcome": "promoted",
            "entity": {"kind": "specimen", "id": slug, "revision": new_revision},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="analyze.native-result",
            writes=[
                JournalWrite(destination.relative_path, None, new_revision, updated),
                JournalWrite(located.relative_path, current, None),
                JournalWrite(invocation_relative(invocation_id), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return result


def _intake_birth(project: Project, payload: dict[str, Any]) -> tuple[LocatedSpecimen, dict[str, Any]]:
    slug = payload["slug"]
    _check_slug(slug)
    value = {
        "schema_version": "2",
        "slug": slug,
        **{field: deepcopy(payload[field]) for field in CREATE_FIELDS},
        "changes": [],
        "decisions": [],
        "rounds": [],
        "assays": [],
        "acceptances": [],
        "events": [],
    }
    located = _matter_location(project, slug)
    _validate_specimen(value, located)
    return located, value


def _slug_vacant(project: Project, slug: str) -> bool:
    try:
        locate(project, slug)
    except IsotopeError as exc:
        if exc.code == "specimen-not-found":
            return True
        if exc.code != "specimen-ambiguous":
            raise
    return False


def record_intake(
    project: Project,
    invocation_id: str,
    *,
    entities: list[dict[str, Any]] | None,
    matter_payload: dict[str, Any] | None,
    source_guard: Callable[[], None],
) -> dict[str, Any]:
    """Commit zero or more matter births or one matter rework plus the compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    with transaction_scope(project):
        invocation = read_invocation(project, invocation_id)
        coordinates = invocation["coordinates"]
        mode = coordinates.get("mode")
        if invocation["status"] == "complete" and invocation["result"] is not None:
            stored = invocation["result"]
            matching = stored.get("status") == "complete"
            durable: dict[str, str] = {}
            expected: dict[str, str] = {}
            requested_slugs: list[str] = []
            entity = stored.get("entity") or {}
            try:
                if mode == "capture":
                    for payload in entities or []:
                        _, value = _intake_birth(project, payload)
                        expected[payload["slug"]] = revision(value)
                        requested_slugs.append(payload["slug"])
                    durable = dict(entity.get("revisions") or {})
                else:
                    located = locate(project, coordinates["slug"])
                    value, _ = read_validated(located)
                    requested_slugs = [coordinates["slug"]]
                    expected = dict(entity.get("revisions") or {})
                    durable = expected if value["matter"] == matter_payload else {}
            except IsotopeError:
                matching = False
            if (
                matching
                and entity.get("kind") == "matter"
                and requested_slugs == entity.get("slugs")
                and expected == durable
                and expected == entity.get("revisions")
            ):
                return stored
            raise IsotopeError("intake-race", "The invocation already recorded a different Intake result.", EXIT_CONFLICT, {"invocation": invocation_id})
        mismatches = []
        if invocation["reaction"] != "intake":
            mismatches.append("reaction")
        expected_effect = f"specimen.matter.{'create' if mode == 'capture' else 'rework'}"
        if mode not in ("capture", "rework") or expected_effect not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Intake provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation_id, "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Intake invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation_id, "status": invocation["status"]})
        source_guard()
        writes = []
        revisions: dict[str, str] = {}
        if mode == "capture":
            slugs = [payload["slug"] for payload in entities or []]
            for payload in entities or []:
                if not _slug_vacant(project, payload["slug"]):
                    raise IsotopeError("intake-race", "A specimen already owns an Intake capture slug.", EXIT_CONFLICT, {"slug": payload["slug"]})
                located, value = _intake_birth(project, payload)
                revisions[payload["slug"]] = revision(value)
                writes.append(JournalWrite(located.relative_path, None, revision(value), value))
            outcome = "captured"
        else:
            slug = coordinates["slug"]
            slugs = [slug]
            located = locate(project, slug)
            if located.stage != "matter":
                raise IsotopeError("wrong-stage", "Intake rework rewrites a matter specimen in place.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
            validate_schema("matter", matter_payload)
            value, current = read_validated(located)
            updated = {**value, "matter": deepcopy(matter_payload)}
            _validate_specimen(updated, located)
            revisions[slug] = revision(updated)
            if revisions[slug] != current:
                writes.append(JournalWrite(located.relative_path, current, revisions[slug], updated))
            outcome = "reworked"
        result = {
            "status": "complete",
            "outcome": outcome,
            "entity": {"kind": "matter", "slugs": slugs, "revisions": revisions},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        writes.append(JournalWrite(invocation_relative(invocation_id), revision(invocation), revision(updated_invocation), updated_invocation))
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="intake.native-result",
            writes=writes,
        )
        return result


def record_acceptance(
    project: Project,
    slug: str,
    *,
    expected_revision: str,
    reason: str,
    entity: dict[str, Any],
    source_guard: Callable[[], None],
) -> tuple[LocatedSpecimen, str, dict, dict]:
    """Commit one native Acceptance, operating tail, and compact result atomically."""
    from .invocations import invocation_relative, read_invocation

    if not isinstance(reason, str) or not reason:
        _invalid_input("/reason", "reason must be a non-empty sentence.")
    with transaction_scope(project):
        located = locate(project, slug)
        if located.stage != "flux":
            raise IsotopeError("wrong-stage", "Acceptance records only into an armed flux specimen.", EXIT_REFUSED, {"slug": slug, "stage": located.stage})
        value, current = read_validated(located)
        invocation = read_invocation(project, entity.get("invocation_id", ""))
        target = next((item for item in value["acceptances"] if item["number"] == entity.get("number")), None)
        if invocation["status"] == "complete" and invocation["result"] is not None:
            if target == entity:
                return located, current, value["events"][-1], invocation["result"]
            raise IsotopeError("acceptance-race", "The invocation already recorded a different Acceptance result.", EXIT_CONFLICT, {"acceptance": entity.get("number")})
        operating = require_armed(project, slug, current)
        _check_expected(expected_revision, current)
        source_guard()
        validate_schema("acceptance", entity)
        coordinates = invocation["coordinates"]
        mismatches = []
        if invocation["reaction"] != "acceptance":
            mismatches.append("reaction")
        if coordinates.get("slug") != slug or coordinates.get("acceptance") != entity["number"] or entity["id"] != f"A{entity['number']}":
            mismatches.append("coordinates")
        if invocation["protocol_version"] != entity.get("reaction_protocol_version"):
            mismatches.append("protocol_version")
        if invocation["source_revisions"] != entity.get("source_revisions"):
            mismatches.append("source_revisions")
        if entity.get("source_revisions", {}).get("specimen") != current:
            mismatches.append("specimen_revision")
        snapshot, snapshot_revision = _live_snapshot(project, operating)
        if entity.get("acceptance_snapshot") != snapshot or entity.get("source_revisions", {}).get("acceptance_snapshot") != snapshot_revision:
            mismatches.append("acceptance_snapshot")
        actor = entity.get("actor", {})
        if actor.get("host") != invocation["host"] or actor.get("model") != invocation["model"] or actor.get("reaction") != "acceptance":
            mismatches.append("actor")
        if "specimen.acceptance.append" not in invocation["allowed_effects"]:
            mismatches.append("allowed_effects")
        if invocation["review_snapshot_revision"] is not None:
            mismatches.append("review_snapshot_revision")
        if mismatches:
            raise IsotopeError("invocation-mismatch", "The Acceptance provenance does not match the frozen invocation.", EXIT_CONFLICT, {"invocation": invocation["id"], "fields": mismatches})
        if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
            raise IsotopeError("invocation-not-completable", "The Acceptance invocation is already terminal.", EXIT_CONFLICT, {"invocation": invocation["id"], "status": invocation["status"]})
        expected_number = len(value["acceptances"]) + 1
        if target is not None or entity["number"] != expected_number:
            raise IsotopeError("acceptance-race", "A different result owns the Acceptance destination.", EXIT_CONFLICT, {"expected": expected_number, "actual": entity["number"]})
        _require_changes_passed(value)
        value["acceptances"].append(entity)
        event = {
            "schema_version": "2",
            "id": f"E{len(value['events']) + 1}",
            "order": len(value["events"]) + 1,
            "operation": "add",
            "entity_kind": "acceptance",
            "entity_id": entity["id"],
            "reason": reason,
            "reaction": "acceptance",
            "owner": OWNER_SKILL,
            "before_hash": None,
            "after_hash": revision(entity),
            "prior_revision": current,
        }
        value["events"].append(event)
        _validate_specimen(value, located)
        new_revision = revision(value)
        tail = advanced_tail(operating, new_revision)
        result = {
            "status": "complete",
            "outcome": entity["verdict"],
            "entity": {"kind": "acceptance", "id": entity["id"], "revision": revision(entity)},
        }
        updated_invocation = deepcopy(invocation)
        updated_invocation["status"] = "complete"
        updated_invocation["result"] = result
        validate_schema("invocation", updated_invocation)
        run_transaction(
            project,
            journal_type="brokered-result",
            operation="acceptance.native-result",
            writes=[
                JournalWrite(located.relative_path, current, new_revision, value),
                JournalWrite(OPERATING_RELATIVE, revision(operating), revision(tail), tail),
                JournalWrite(invocation_relative(invocation["id"]), revision(invocation), revision(updated_invocation), updated_invocation),
            ],
        )
        return located, new_revision, event, result


def _require_invocation(
    project: Project, assay: dict, slug: str, completion_capability: str
) -> None:
    from .invocations import (  # local import keeps module layering acyclic
        capability_matches,
        read_invocation,
    )

    invocation = read_invocation(project, assay["invocation_id"])
    coordinates = invocation["coordinates"]
    matches = (
        invocation["reaction"] == "review"
        and coordinates.get("slug") == slug
        and coordinates.get("change") == assay["change"]
        and coordinates.get("round") == assay["round"]
    )
    if not matches:
        raise IsotopeError(
            "invocation-mismatch",
            "The assay's invocation does not target this specimen, change, and round.",
            EXIT_CONFLICT,
            {"invocation": assay["invocation_id"], "coordinates": coordinates},
        )
    mismatches = []
    if invocation["protocol_version"] != assay["reaction_protocol_version"]:
        mismatches.append("protocol_version")
    if invocation["source_revisions"] != assay["source_revisions"]:
        mismatches.append("source_revisions")
    if invocation["review_snapshot_revision"] != assay["review_snapshot_revision"]:
        mismatches.append("review_snapshot_revision")
    if invocation["host"] != assay["actor"]["host"]:
        mismatches.append("actor.host")
    if invocation["model"] != assay["actor"]["model"]:
        mismatches.append("actor.model")
    if "specimen.assay.append" not in invocation["allowed_effects"]:
        mismatches.append("allowed_effects")
    if mismatches:
        raise IsotopeError(
            "invocation-mismatch",
            "The assay provenance does not match the frozen invocation.",
            EXIT_CONFLICT,
            {"invocation": assay["invocation_id"], "fields": mismatches},
        )
    if invocation["status"] not in ("created", "running") or invocation["result"] is not None:
        raise IsotopeError(
            "invocation-not-completable",
            "The invocation is not in a state that can broker a new assay.",
            EXIT_CONFLICT,
            {"invocation": assay["invocation_id"], "status": invocation["status"]},
        )
    if not capability_matches(
        completion_capability, invocation["completion_capability_hash"]
    ):
        raise IsotopeError(
            "completion-capability-invalid",
            "The retained completion capability does not authorize this assay.",
            EXIT_REFUSED,
            {"invocation": assay["invocation_id"]},
        )


def acceptance_append(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        _require_changes_passed(value)
        validate_schema("acceptance", entity)
        value["acceptances"].append(entity)
        return _EventSpec("add", "acceptance", str(entity["id"]), None, entity)

    return _flux_transaction(project, slug, payload, "acceptance", "specimen.acceptance.append", apply)


def decision_add(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema("decision", entity)
        value["decisions"].append(entity)
        return _EventSpec("add", "decision", entity["id"], None, entity)

    return _flux_transaction(project, slug, payload, "decision", "specimen.decision.add", apply)


def decision_supersede(project: Project, slug: str, decision_id: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema("decision", entity)
        target = next((item for item in value["decisions"] if item["id"] == decision_id), None)
        if target is None:
            raise IsotopeError("decision-not-found", f"No decision {decision_id!r} exists.", EXIT_NOT_FOUND, {"decision": decision_id})
        if entity["id"] != decision_id:
            _invalid_input("/entity/id", "A superseded decision keeps its stable id.")
        if entity["question"] != target["question"]:
            _invalid_input("/entity/question", "A superseded decision keeps its original question.")
        before = deepcopy(target)
        value["decisions"][value["decisions"].index(target)] = entity
        return _EventSpec("replace", "decision", decision_id, before, entity)

    return _flux_transaction(project, slug, payload, "decision", "specimen.decision.supersede", apply)


def change_revise(project: Project, slug: str, number: int, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema("change", entity)
        if entity["number"] != number:
            _invalid_input("/entity/number", "A revised change keeps its number.")
        for index, item in enumerate(value["changes"]):
            if item["number"] == number:
                before = item
                value["changes"][index] = entity
                return _EventSpec("replace", "change", str(number), before, entity)
        raise IsotopeError("change-not-found", f"No change {number} exists.", EXIT_NOT_FOUND, {"change": number})

    return _flux_transaction(project, slug, payload, "design", "specimen.change.revise", apply)


def outcome_set(project: Project, slug: str, payload: Any) -> tuple[LocatedSpecimen, str, dict]:
    def apply(value: dict, entity: Any) -> _EventSpec:
        validate_schema("outcome", entity)
        require_mapped_targets(project, entity["doc_targets"])
        before = value.get("outcome")
        value["outcome"] = entity
        return _EventSpec("add" if before is None else "replace", "outcome", "outcome", before, entity)

    return _flux_transaction(project, slug, payload, "expression", "specimen.outcome.set", apply)


# --- Record-level reads --------------------------------------------------------------------

_LOG_ABSTRACT_FIELD = {
    "analysis": "goal",
    "design-context": "context",
    "acceptance-criterion": "criterion",
    "verification": "instruction",
    "round": "abstract",
    "assay": "abstract",
    "acceptance": "abstract",
    "decision": "question",
    "change": "title",
    "outcome": "landed",
}


def change_record(value: dict, number: int) -> dict:
    for item in value["changes"]:
        if item["number"] == number:
            return item
    raise IsotopeError("change-not-found", f"No change {number} exists.", EXIT_NOT_FOUND, {"change": number})


def decision_records(value: dict) -> list[dict]:
    return [{"in_force": True, "decision": item} for item in value["decisions"]]


def outcome_packet(value: dict) -> dict:
    if value.get("outcome") is None:
        raise IsotopeError("outcome-missing", "The specimen has no outcome record yet.", EXIT_NOT_FOUND, {"slug": value["slug"]})
    return {
        "slug": value["slug"],
        "goal": value["goal"],
        "outcome": value["outcome"],
        "decisions_in_force": list(value["decisions"]),
        "doc_targets": value["outcome"]["doc_targets"],
    }


def _resolve_entity(value: dict, kind: str, entity_id: str) -> Any:
    if kind == "analysis":
        return _analysis_entity(value)
    if kind == "design-context":
        return {"context": value["context"]} if value.get("context") is not None else None
    if kind == "acceptance-criterion":
        return next((item for item in value.get("acceptance_criteria", []) if item["id"] == entity_id), None)
    if kind == "verification":
        return next((item for item in value.get("verification", []) if item["id"] == entity_id), None)
    if kind == "outcome":
        return value.get("outcome")
    if kind == "change":
        return next((item for item in value["changes"] if str(item["number"]) == entity_id), None)
    pool = {"round": "rounds", "assay": "assays", "acceptance": "acceptances", "decision": "decisions"}[kind]
    return next((item for item in value[pool] if item["id"] == entity_id), None)


def log_records(value: dict, view: str) -> list[dict]:
    """The events array is the chronological log; each record joins its current entity."""
    records = []
    for event in value["events"]:
        kind = event["entity_kind"]
        entity = _resolve_entity(value, kind, event["entity_id"])
        record = {
            "id": event["id"],
            "kind": kind,
            "entity_id": event["entity_id"],
            "operation": event["operation"],
            "reaction": event["reaction"],
            "change": _log_change(kind, event, entity),
            "round": _log_round(kind, entity),
            "status": _log_status(kind, entity),
            "abstract": entity[_LOG_ABSTRACT_FIELD[kind]] if entity is not None else event["reason"],
        }
        if view == "full":
            record["event"] = event
            record["entity"] = entity
        records.append(record)
    return records


def _log_change(kind: str, event: dict, entity: Any) -> int | None:
    if kind in ("round", "assay") and entity is not None:
        return entity["change"]
    if kind == "change":
        return int(event["entity_id"])
    return None


def _log_round(kind: str, entity: Any) -> int | None:
    if entity is None:
        return None
    if kind == "round":
        return entity["number"]
    if kind == "assay":
        return entity["round"]
    return None


def _log_status(kind: str, entity: Any) -> str | None:
    if entity is None:
        return None
    if kind == "round":
        return entity["status"]
    if kind == "assay":
        return entity["outcome"]
    if kind == "acceptance":
        return entity["verdict"]
    return None


def filter_log(
    records: list[dict],
    *,
    change: int | None = None,
    round_number: int | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> list[dict]:
    def keep(record: dict) -> bool:
        if change is not None and record["change"] != change:
            return False
        if round_number is not None and record["round"] != round_number:
            return False
        if kind is not None and record["kind"] != kind:
            return False
        if status is not None and record["status"] != status:
            return False
        return True

    return [record for record in records if keep(record)]


def paginate(records: list[dict], *, source_revision: str, limit: int, cursor: str | None) -> tuple[list[dict], dict]:
    offset = 0
    if cursor is not None:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
            offset = decoded["offset"]
            bound = decoded["revision"]
        except Exception as exc:
            raise IsotopeError("invalid-cursor", "The cursor could not be decoded.", EXIT_USAGE, {"cursor": cursor}) from exc
        if not isinstance(offset, int) or offset < 0 or not isinstance(bound, str):
            raise IsotopeError("invalid-cursor", "The cursor could not be decoded.", EXIT_USAGE, {"cursor": cursor})
        if bound != source_revision:
            raise IsotopeError(
                "stale-cursor",
                "The source changed since the cursor was issued; restart from the first page.",
                EXIT_CONFLICT,
                {"cursor_revision": bound, "revision": source_revision},
            )
    window = records[offset:offset + limit]
    next_cursor = None
    if offset + limit < len(records):
        token = json.dumps({"offset": offset + limit, "revision": source_revision}, sort_keys=True, separators=(",", ":"))
        next_cursor = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")
    return window, {"limit": limit, "next_cursor": next_cursor}
