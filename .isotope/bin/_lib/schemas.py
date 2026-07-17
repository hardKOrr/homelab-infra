"""Published entity schemas and a small pure-stdlib schema validator."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .errors import EXIT_MALFORMED, EXIT_NOT_FOUND, IsotopeError


DRAFT = "https://json-schema.org/draft/2020-12/schema"
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+)*$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
INVOCATION_ID_PATTERN = r"^I[1-9][0-9]*$"
QUESTION_ID_PATTERN = r"^Q[1-9][0-9]*$"

# Literal reaction IDs and owner-skill identities from the glossary.
REACTIONS = (
    "intake",
    "analyze",
    "design",
    "decision",
    "construction",
    "review",
    "acceptance",
    "expression",
)
OWNER_SKILLS = ("architect", "operate")
HOSTS = ("claude", "codex")


def _object(title: str, entity: str, version: str, properties: dict, required: list[str]) -> dict:
    return {
        "$schema": DRAFT,
        "$id": f"isotope://schema/{entity}/{version}",
        "title": title,
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def _string(pattern: str | None = None) -> dict:
    result: dict[str, Any] = {"type": "string", "minLength": 1}
    if pattern:
        result["pattern"] = pattern
    return result


def _strings(pattern: str | None = None) -> dict:
    return {"type": "array", "items": _string(pattern), "uniqueItems": True}


def _nullable_string(pattern: str | None = None) -> dict:
    result: dict[str, Any] = {"type": ["string", "null"], "minLength": 1}
    if pattern:
        result["pattern"] = pattern
    return result


PROJECT_SCHEMA = _object(
    "Isotope project identity",
    "project",
    "1",
    {"root": _string(), "git_common_dir": _string()},
    ["root", "git_common_dir"],
)
ERROR_SCHEMA = _object(
    "Isotope command error",
    "error",
    "1",
    {"code": _string(), "message": _string(), "details": {"type": "object"}},
    ["code", "message", "details"],
)
ENVELOPE_SCHEMA = _object(
    "Isotope command envelope",
    "envelope",
    "1",
    {
        "schema_version": {"const": "1"},
        "operation": _string(),
        "status": {"enum": ["ok", "error"]},
        "project": {"type": ["object", "null"]},
        "source": {"type": ["object", "null"]},
        "data": {},
        "page": {"type": ["object", "null"]},
        "error": {"type": "object"},
    },
    ["schema_version", "operation", "status", "project", "source", "data", "page"],
)

CHANGE_SCHEMA = _object(
    "Isotope planned change",
    "change",
    "2",
    {
        "schema_version": {"const": "2"},
        "number": {"type": "integer", "minimum": 1},
        "title": _string(),
        "files": _strings(),
        "steps": _strings(),
        "tests": _strings(),
        "commit": _string(),
        "model": {"enum": ["sonnet", "opus", "fable"]},
        "inline": {"type": "boolean"},
    },
    ["schema_version", "number", "title", "files", "steps", "tests", "commit"],
)

DECISION_TRIGGER_SCHEMA = _object(
    "Isotope decision trigger",
    "decision-trigger",
    "1",
    {
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "question_id": _string(QUESTION_ID_PATTERN),
    },
    ["invocation_id", "question_id"],
)
DECISION_ACTOR_SCHEMA = _object(
    "Isotope Decision actor provenance",
    "decision-actor",
    "1",
    {
        "host": {"enum": list(HOSTS)},
        "model": _nullable_string(),
        "reaction": {"const": "decision"},
    },
    ["host", "model", "reaction"],
)
DECISION_SCHEMA = _object(
    "Isotope decision",
    "decision",
    "2",
    {
        "schema_version": {"const": "2"},
        "id": _string(r"^D[1-9][0-9]*$"),
        "question": _string(),
        "decision": _string(),
        "rationale": _string(),
        "trigger": DECISION_TRIGGER_SCHEMA,
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "reaction_protocol_version": _string(),
        "source_revisions": {"type": "object"},
        "actor": DECISION_ACTOR_SCHEMA,
    },
    ["schema_version", "id", "question", "decision", "rationale"],
)

EVIDENCE_SCHEMA = _object(
    "Isotope command evidence",
    "evidence",
    "1",
    {
        "gate_id": _string(),
        "command": _string(),
        "exit_code": {"type": "integer"},
        "output": {"type": "string"},
        "cwd": _string(),
    },
    ["gate_id", "command", "exit_code", "output"],
)

SNAPSHOT_INDEX_ENTRY_SCHEMA = _object(
    "Isotope snapshot index entry",
    "snapshot-index-entry",
    "1",
    {"mode": _string(), "path": _string(), "blob": _string(COMMIT_PATTERN), "stage": {"type": "integer", "minimum": 0}},
    ["mode", "path", "blob", "stage"],
)
SNAPSHOT_TRACKED_ENTRY_SCHEMA = _object(
    "Isotope snapshot tracked worktree entry",
    "snapshot-tracked-entry",
    "1",
    {"path": _string(), "status": {"enum": ["modified", "deleted"]}, "digest": _nullable_string(HASH_PATTERN)},
    ["path", "status", "digest"],
)
SNAPSHOT_UNTRACKED_ENTRY_SCHEMA = _object(
    "Isotope snapshot untracked entry",
    "snapshot-untracked-entry",
    "1",
    {"path": _string(), "digest": _string(HASH_PATTERN)},
    ["path", "digest"],
)

REVIEW_SNAPSHOT_SCHEMA = _object(
    "Isotope Review snapshot manifest",
    "review-snapshot",
    "1",
    {
        "schema_version": {"const": "1"},
        "base_commit": _string(COMMIT_PATTERN),
        "head_commit": _string(COMMIT_PATTERN),
        "index": {"type": "array", "items": SNAPSHOT_INDEX_ENTRY_SCHEMA},
        "tracked": {"type": "array", "items": SNAPSHOT_TRACKED_ENTRY_SCHEMA},
        "untracked": {"type": "array", "items": SNAPSHOT_UNTRACKED_ENTRY_SCHEMA},
    },
    ["schema_version", "base_commit", "head_commit", "index", "tracked", "untracked"],
)

CONSTRUCTION_ACTOR_SCHEMA = _object(
    "Isotope Construction actor provenance",
    "construction-actor",
    "1",
    {
        "host": {"enum": list(HOSTS)},
        "model": _nullable_string(),
        "reaction": {"const": "construction"},
    },
    ["host", "model", "reaction"],
)

ROUND_SCHEMA = _object(
    "Isotope construction round",
    "round",
    "2",
    {
        "schema_version": {"const": "2"},
        "id": _string(r"^C[1-9][0-9]*-R[1-9][0-9]*$"),
        "change": {"type": "integer", "minimum": 1},
        "number": {"type": "integer", "minimum": 1},
        "abstract": _string(),
        "status": {"enum": ["complete", "blocked", "decision-needed"]},
        "details": _string(),
        "files_touched": _strings(),
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "decision_questions": _strings(),
        "blockers": _strings(),
        "review_snapshot": REVIEW_SNAPSHOT_SCHEMA,
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "reaction_protocol_version": _string(),
        "source_revisions": {"type": "object"},
        "actor": CONSTRUCTION_ACTOR_SCHEMA,
    },
    ["schema_version", "id", "change", "number", "abstract", "status", "details", "files_touched", "evidence"],
)

ASSAY_ACTOR_SCHEMA = _object(
    "Isotope assay actor provenance",
    "assay-actor",
    "1",
    {
        "host": {"enum": list(HOSTS)},
        "model": _nullable_string(),
        "reaction": {"const": "review"},
    },
    ["host", "model", "reaction"],
)

ASSAY_SCHEMA = _object(
    "Isotope review assay",
    "assay",
    "2",
    {
        "schema_version": {"const": "2"},
        "id": _string(r"^C[1-9][0-9]*-R[1-9][0-9]*-A$"),
        "change": {"type": "integer", "minimum": 1},
        "round": {"type": "integer", "minimum": 1},
        "outcome": {"enum": ["PASS", "CHANGES"]},
        "abstract": _string(),
        "findings": _strings(),
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "reaction_protocol_version": _string(),
        "source_revisions": {"type": "object"},
        "review_snapshot_revision": _string(HASH_PATTERN),
        "actor": ASSAY_ACTOR_SCHEMA,
    },
    [
        "schema_version", "id", "change", "round", "outcome", "abstract", "findings",
        "invocation_id", "reaction_protocol_version", "source_revisions",
        "review_snapshot_revision", "actor",
    ],
)

CRITERION_RESULT_SCHEMA = _object(
    "Isotope acceptance criterion result",
    "criterion-result",
    "1",
    {
        "criterion_id": _string(r"^AC[1-9][0-9]*$"),
        "status": {"enum": ["PASS", "FAIL"]},
        "evidence": _string(),
    },
    ["criterion_id", "status", "evidence"],
)
VERIFICATION_RESULT_SCHEMA = _object(
    "Isotope verification result",
    "verification-result",
    "1",
    {
        "verification_id": _string(r"^V[1-9][0-9]*$"),
        "status": {"enum": ["PASS", "FAIL"]},
        "evidence": _string(),
    },
    ["verification_id", "status", "evidence"],
)
ACCEPTANCE_FINDING_SCHEMA = _object(
    "Isotope acceptance finding",
    "acceptance-finding",
    "1",
    {"change": {"type": "integer", "minimum": 1}, "text": _string()},
    ["change", "text"],
)

ACCEPTANCE_ACTOR_SCHEMA = _object(
    "Isotope Acceptance actor provenance",
    "acceptance-actor",
    "1",
    {
        "host": {"enum": list(HOSTS)},
        "model": _nullable_string(),
        "reaction": {"const": "acceptance"},
    },
    ["host", "model", "reaction"],
)

ACCEPTANCE_SCHEMA = _object(
    "Isotope whole-specimen acceptance",
    "acceptance",
    "2",
    {
        "schema_version": {"const": "2"},
        "id": _string(r"^A[1-9][0-9]*$"),
        "number": {"type": "integer", "minimum": 1},
        "verdict": {"enum": ["PASS", "CHANGES"]},
        "abstract": _string(),
        "criteria": {"type": "array", "items": CRITERION_RESULT_SCHEMA},
        "verification": {"type": "array", "items": VERIFICATION_RESULT_SCHEMA},
        "findings": {"type": "array", "items": ACCEPTANCE_FINDING_SCHEMA},
        "acceptance_snapshot": REVIEW_SNAPSHOT_SCHEMA,
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "reaction_protocol_version": _string(),
        "source_revisions": {"type": "object"},
        "actor": ACCEPTANCE_ACTOR_SCHEMA,
    },
    ["schema_version", "id", "number", "verdict", "abstract", "criteria", "verification", "findings"],
)

DOC_TARGET_SCHEMA = _object(
    "Isotope documentation target",
    "doc-target",
    "1",
    {"concept": _string(), "path": _string(), "section_id": _string()},
    ["concept", "path", "section_id"],
)

DOC_MAP_ENTRY_SCHEMA = _object(
    "Isotope documentation map entry",
    "doc-map-entry",
    "1",
    {
        "concept": _string(),
        "path": _string(),
        "section_id": _string(r"^[a-z0-9][a-z0-9-]*$"),
    },
    ["concept", "path", "section_id"],
)

MANIFEST_SCHEMA = _object(
    "Isotope consumer manifest",
    "manifest",
    "1",
    {
        "schema_version": {"const": "1"},
        "autonomy": {"enum": ["declared", "repo"]},
        "gates": {"type": "object"},
        "allow": _strings(),
        "docs": {"type": "array", "items": DOC_MAP_ENTRY_SCHEMA, "uniqueItems": True},
        "models": {"type": "object"},
        "tools": {"type": ["object", "string", "null"]},
    },
    ["schema_version"],
)

EXPRESSED_TARGET_SCHEMA = _object(
    "Isotope expressed documentation target",
    "expressed-target",
    "1",
    {"concept": _string(), "path": _string(), "section_id": _string(), "revision": _string(HASH_PATTERN)},
    ["concept", "path", "section_id", "revision"],
)

EXPRESSION_SCHEMA = _object(
    "Isotope outcome expression evidence",
    "expression",
    "1",
    {
        "abstract": _string(),
        "targets": {"type": "array", "items": EXPRESSED_TARGET_SCHEMA},
        "invocation_id": _string(INVOCATION_ID_PATTERN),
        "reaction_protocol_version": _string(),
        "source_revisions": {"type": "object"},
        "actor": _object(
            "Isotope expression actor",
            "expression-actor",
            "1",
            {"host": {"enum": list(HOSTS)}, "model": {"type": ["string", "null"]}, "reaction": {"const": "expression"}},
            ["host", "model", "reaction"],
        ),
    },
    ["abstract", "targets", "invocation_id", "reaction_protocol_version", "source_revisions", "actor"],
)

OUTCOME_SCHEMA = _object(
    "Isotope deployment outcome",
    "outcome",
    "2",
    {
        "schema_version": {"const": "2"},
        "landed": _string(),
        "verification_summary": _string(),
        "doc_targets": {"type": "array", "items": DOC_TARGET_SCHEMA},
        "notes": _string(),
        "expression": EXPRESSION_SCHEMA,
    },
    ["schema_version", "landed", "verification_summary", "doc_targets"],
)

EVENT_SCHEMA = _object(
    "Isotope audited specimen event",
    "event",
    "2",
    {
        "schema_version": {"const": "2"},
        "id": _string(r"^E[1-9][0-9]*$"),
        "order": {"type": "integer", "minimum": 1},
        "operation": {"enum": ["add", "replace"]},
        "entity_kind": {"enum": [
            "analysis", "design-context", "acceptance-criterion", "change",
            "verification", "decision", "round", "assay", "acceptance", "outcome",
        ]},
        "entity_id": _string(),
        "reason": _string(),
        "reaction": {"enum": list(REACTIONS)},
        "owner": {"enum": list(OWNER_SKILLS)},
        "before_hash": {"type": ["string", "null"], "pattern": HASH_PATTERN},
        "after_hash": _string(HASH_PATTERN),
        "prior_revision": _string(HASH_PATTERN),
    },
    [
        "schema_version", "id", "order", "operation", "entity_kind", "entity_id",
        "reason", "reaction", "owner", "before_hash", "after_hash", "prior_revision",
    ],
)

ACCEPTANCE_CRITERION_SCHEMA = _object(
    "Isotope acceptance criterion",
    "acceptance-criterion",
    "1",
    {"id": _string(r"^AC[1-9][0-9]*$"), "criterion": _string()},
    ["id", "criterion"],
)
VERIFICATION_STEP_SCHEMA = _object(
    "Isotope verification step",
    "verification-step",
    "1",
    {"id": _string(r"^V[1-9][0-9]*$"), "instruction": _string(), "gate_id": _string()},
    ["id", "instruction"],
)
MATTER_SCHEMA = _object(
    "Isotope specimen matter",
    "matter",
    "2",
    {"content": _string(), "source": _string()},
    ["content"],
)
DESIGN_SCHEMA = _object(
    "Isotope specimen design",
    "design",
    "1",
    {
        "context": _string(),
        "acceptance_criteria": {"type": "array", "items": ACCEPTANCE_CRITERION_SCHEMA, "minItems": 1},
        "changes": {"type": "array", "items": CHANGE_SCHEMA, "minItems": 1},
        "verification": {"type": "array", "items": VERIFICATION_STEP_SCHEMA, "minItems": 1},
    },
    ["context", "acceptance_criteria", "changes", "verification"],
)
ANALYSIS_SCHEMA = _object(
    "Isotope specimen analysis",
    "analysis",
    "1",
    {
        "type": {"enum": ["feature", "fix", "refactor", "config", "docs"]},
        "depends_on": _strings(SLUG_PATTERN),
        "spec_provenance": _strings(),
        "prerequisites": _strings(),
        "goal": _string(),
    },
    ["type", "depends_on", "spec_provenance", "prerequisites", "goal"],
)

SPECIMEN_SCHEMA = _object(
    "Isotope specimen",
    "specimen",
    "2",
    {
        "schema_version": {"const": "2"},
        "slug": _string(SLUG_PATTERN),
        "matter": MATTER_SCHEMA,
        "demoted_from_revision": _string(HASH_PATTERN),
        "type": {"enum": ["feature", "fix", "refactor", "config", "docs"]},
        "depends_on": _strings(SLUG_PATTERN),
        "spec_provenance": _strings(),
        "prerequisites": _strings(),
        "goal": _string(),
        "context": _string(),
        "acceptance_criteria": {"type": "array", "items": ACCEPTANCE_CRITERION_SCHEMA},
        "changes": {"type": "array", "items": CHANGE_SCHEMA},
        "decisions": {"type": "array", "items": DECISION_SCHEMA},
        "verification": {"type": "array", "items": VERIFICATION_STEP_SCHEMA},
        "rounds": {"type": "array", "items": ROUND_SCHEMA},
        "assays": {"type": "array", "items": ASSAY_SCHEMA},
        "acceptances": {"type": "array", "items": ACCEPTANCE_SCHEMA},
        "events": {"type": "array", "items": EVENT_SCHEMA},
        "outcome": {"type": ["object", "null"]},
    },
    [
        "schema_version", "slug", "matter", "type", "depends_on", "spec_provenance",
        "prerequisites", "goal", "changes", "decisions", "rounds", "assays",
        "acceptances", "events",
    ],
)

OPERATING_SCHEMA = _object(
    "Isotope armed operating state",
    "operating",
    "1",
    {
        "schema_version": {"const": "1"},
        "slug": _string(SLUG_PATTERN),
        "branch": _string(),
        "base_commit": _string(COMMIT_PATTERN),
        "specimen_revision": _string(HASH_PATTERN),
        "state": {"enum": ["armed", "parked", "landing", "landed"]},
        "parked_head": _string(COMMIT_PATTERN),
        "target_branch": _string(),
        "operation_head": _string(COMMIT_PATTERN),
        "landed_commit": _string(COMMIT_PATTERN),
        "landing_reason": _string(),
    },
    ["schema_version", "slug", "branch", "base_commit", "specimen_revision", "state"],
)

SEMANTIC_COMMIT_INPUT_SCHEMA = _object(
    "Isotope semantic commit input", "semantic-commit-input", "1",
    {
        "expected_head": _string(COMMIT_PATTERN),
        "files": _strings(),
        "reason": _string(),
    },
    ["expected_head", "files", "reason"],
)

DEPLOY_INPUT_SCHEMA = _object(
    "Isotope deploy input", "deploy-input", "1",
    {
        "expected_head": _string(COMMIT_PATTERN),
        "target_branch": _string(),
        "reason": _string(),
    },
    ["expected_head", "target_branch", "reason"],
)

REGISTRY_MODEL_SCHEMA = _object(
    "Isotope registry model option", "registry-model", "1",
    {
        "id": _string(), "enabled": {"type": "boolean"},
        "reactions": _strings(), "cost": _nullable_string(),
        "rate": _nullable_string(), "reasoning": _nullable_string(),
    },
    ["id", "enabled", "reactions"],
)
REGISTRY_HOST_SCHEMA = _object(
    "Isotope registry host", "registry-host", "1",
    {
        "enabled": {"type": "boolean"}, "available": {"type": ["boolean", "null"]},
        "default_model": _nullable_string(),
        "models": {"type": "array", "items": REGISTRY_MODEL_SCHEMA},
    },
    ["enabled", "available", "default_model", "models"],
)
REGISTRY_SCHEMA = _object(
    "Isotope consumer registry", "registry", "1",
    {"schema_version": {"const": "1"}, "hosts": {"type": "object"}},
    ["schema_version", "hosts"],
)

SYNTHESIS_FILE_SCHEMA = _object(
    "Isotope synthesized file", "synthesis-file", "1",
    {"path": _string(), "revision": _string(HASH_PATTERN), "kind": _string(), "host": {"type": ["string", "null"]}},
    ["path", "revision", "kind", "host"],
)
SYNTHESIS_SCHEMA = _object(
    "Isotope generated-asset synthesis", "synthesis", "1",
    {
        "schema_version": {"const": "1"}, "source_version": _string(VERSION_PATTERN),
        "targets": {"type": "object"},
        "files": {"type": "array", "items": SYNTHESIS_FILE_SCHEMA},
        "observations": {"type": "object"},
    },
    ["schema_version", "source_version", "targets", "files", "observations"],
)

JOURNAL_WRITE_SCHEMA = _object(
    "Isotope journal write",
    "journal-write",
    "1",
    {
        "path": _string(),
        "prior_revision": _nullable_string(HASH_PATTERN),
        "new_revision": _nullable_string(HASH_PATTERN),
        "format": {"enum": ["json", "text"]},
        "value": {},
    },
    ["path", "prior_revision", "new_revision"],
)

JOURNAL_SCHEMA = _object(
    "Isotope typed transaction journal",
    "journal",
    "1",
    {
        "schema_version": {"const": "1"},
        "type": {"enum": ["specimen", "operating", "setup", "invocation", "quanta", "brokered-result"]},
        "operation": _string(),
        "writes": {"type": "array", "items": JOURNAL_WRITE_SCHEMA, "minItems": 1},
    },
    ["schema_version", "type", "operation", "writes"],
)

QUANTUM_ID_PATTERN = r"^Q[1-9][0-9]*$"

QUANTUM_SCHEMA = _object(
    "Isotope quantum evidence record",
    "quantum",
    "1",
    {
        "schema_version": {"const": "1"},
        "id": _string(QUANTUM_ID_PATTERN),
        "type": {"enum": ["command", "friction", "dialect", "gap"]},
        "payload": {"type": "object"},
        "provenance": {
            "type": "object",
            "required": [],
            "properties": {
                "invocation": _string(INVOCATION_ID_PATTERN),
                "slug": _string(SLUG_PATTERN),
                "change": {"type": "integer", "minimum": 1},
                "round": {"type": "integer", "minimum": 1},
                "reaction": {"enum": list(REACTIONS + OWNER_SKILLS)},
                "host": {"enum": list(HOSTS)},
                "model": _string(),
            },
            "additionalProperties": False,
        },
    },
    ["schema_version", "id", "type", "payload", "provenance"],
)
# Per-type payload shapes ride in $defs: the flat validator ignores them on the
# whole record, and validate_quantum_payload dispatches through them so the
# published schema stays the single authority for both layers.
QUANTUM_SCHEMA["$defs"] = {
    "command-payload": {
        "type": "object",
        "required": ["signature", "exit_code", "effect"],
        "properties": {
            "signature": _string(),
            "exit_code": {"type": "integer"},
            "cwd": _string(),
            "effect": _string(),
        },
        "additionalProperties": False,
    },
    "friction-payload": {
        "type": "object",
        "required": ["condition"],
        "properties": {"condition": _string(), "operation": _string()},
        "additionalProperties": False,
    },
    "dialect-payload": {
        "type": "object",
        "required": ["statement"],
        "properties": {"statement": _string()},
        "additionalProperties": False,
    },
    "gap-payload": {
        "type": "object",
        "required": ["statement"],
        "properties": {"statement": _string(), "surface": {"enum": list(REACTIONS + OWNER_SKILLS)}},
        "additionalProperties": False,
    },
}

VALENCE_TOOL_SCHEMA = _object(
    "Isotope Valence tool descriptor",
    "valence-tool",
    "1",
    {
        "schema_version": {"const": "1"},
        "name": _string(SLUG_PATTERN),
        "description": _string(),
        "evidence": {"type": "array", "items": _string(QUANTUM_ID_PATTERN), "minItems": 1, "uniqueItems": True},
        "input": {
            "type": "object",
            "required": ["parameters"],
            "properties": {"parameters": {"type": "object"}},
            "additionalProperties": False,
        },
        "command": {
            "type": "object",
            "required": ["argv"],
            "properties": {"argv": {"type": "array", "items": _string(), "minItems": 1}},
            "additionalProperties": False,
        },
        "effect": {"enum": ["read-only", "repo-write"]},
        "authority": _strings(),
        "validation": {
            "type": "object",
            "required": ["argv", "expect_exit"],
            "properties": {
                "argv": {"type": "array", "items": _string(), "minItems": 1},
                "expect_exit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    ["schema_version", "name", "description", "evidence", "input", "command", "effect", "authority", "validation"],
)

FEEDBACK_RESULT_VALUE_SCHEMA = {
    "type": ["object", "null"],
    "required": ["status", "outcome", "entity"],
    "properties": {
        "status": {"enum": ["complete", "needs-user", "blocked", "refused", "failed"]},
        "outcome": _nullable_string(),
        "entity": {
            "type": ["object", "null"],
            "required": ["kind"],
            "properties": {
                "kind": _string(),
                "id": _string(),
                "revision": _string(HASH_PATTERN),
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

FEEDBACK_BUNDLE_SCHEMA = _object(
    "Isotope portable feedback bundle",
    "feedback-bundle",
    "1",
    {
        "schema_version": {"const": "1"},
        "kind": {"const": "isotope-feedback"},
        "package_version": _string(VERSION_PATTERN),
        "reaction": {"enum": list(REACTIONS + OWNER_SKILLS) + [None]},
        "matter": _string(),
        "source": {
            "type": "object",
            "required": ["slug", "revision"],
            "properties": {"slug": _string(SLUG_PATTERN), "revision": _string(HASH_PATTERN)},
            "additionalProperties": False,
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["quantum", "invocations"],
                "properties": {
                    "quantum": {"type": "object"},
                    "invocations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "reaction", "status", "result"],
                            "properties": {
                                "id": _string(INVOCATION_ID_PATTERN),
                                "reaction": {"enum": list(REACTIONS)},
                                "status": _string(),
                                "result": FEEDBACK_RESULT_VALUE_SCHEMA,
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    ["schema_version", "kind", "package_version", "reaction", "matter", "source", "evidence"],
)

OBSERVED_STATE_SCHEMA = _object(
    "Isotope observed state fingerprint",
    "observed-state",
    "1",
    {"facts": {"type": "object"}, "fingerprint": _string(HASH_PATTERN)},
    ["facts", "fingerprint"],
)

BLOCKING_CONDITION_SCHEMA = _object(
    "Isotope blocking condition",
    "blocking-condition",
    "1",
    {"condition": _string(), "observed_state": OBSERVED_STATE_SCHEMA},
    ["condition", "observed_state"],
)

QUESTION_SCHEMA = _object(
    "Isotope invocation question",
    "question",
    "1",
    {
        "id": _string(QUESTION_ID_PATTERN),
        "text": _string(),
        "answer": {},
    },
    ["id", "text", "answer"],
)

RESULT_ENTITY_SCHEMA = _object(
    "Isotope compact result entity",
    "result-entity",
    "1",
    {"kind": _string(), "id": _string(), "revision": _string(HASH_PATTERN)},
    ["kind", "id", "revision"],
)

COMPACT_RESULT_SCHEMA = _object(
    "Isotope compact reaction result",
    "compact-result",
    "1",
    {
        "status": {"enum": ["complete", "needs-user", "blocked", "refused", "failed"]},
        "outcome": _nullable_string(),
        "entity": {"type": ["object", "null"]},
    },
    ["status", "outcome", "entity"],
)

INVOCATION_SCHEMA = _object(
    "Isotope catalyst invocation",
    "invocation",
    "1",
    {
        "schema_version": {"const": "1"},
        "id": _string(INVOCATION_ID_PATTERN),
        "reaction": {"enum": list(REACTIONS)},
        "protocol_version": _string(),
        "coordinates": {"type": "object"},
        "host": {"enum": list(HOSTS)},
        "model": _nullable_string(),
        "predecessor": _nullable_string(INVOCATION_ID_PATTERN),
        "source_revisions": {"type": "object"},
        "review_snapshot_revision": _nullable_string(HASH_PATTERN),
        "allowed_effects": _strings(),
        "completion_capability_hash": _string(HASH_PATTERN),
        "status": {"enum": ["created", "running", "needs-user", "blocked", "complete", "refused", "failed"]},
        "questions": {"type": "array", "items": QUESTION_SCHEMA},
        "blocking_condition": {"type": ["object", "null"]},
        "result": {"type": ["object", "null"]},
    },
    [
        "schema_version", "id", "reaction", "protocol_version", "coordinates", "host",
        "model", "predecessor", "source_revisions", "review_snapshot_revision",
        "allowed_effects", "completion_capability_hash", "status", "questions",
        "blocking_condition", "result",
    ],
)

SCHEMAS = {
    "acceptance": ACCEPTANCE_SCHEMA,
    "analysis": ANALYSIS_SCHEMA,
    "assay": ASSAY_SCHEMA,
    "blocking-condition": BLOCKING_CONDITION_SCHEMA,
    "change": CHANGE_SCHEMA,
    "compact-result": COMPACT_RESULT_SCHEMA,
    "decision": DECISION_SCHEMA,
    "deploy-input": DEPLOY_INPUT_SCHEMA,
    "design": DESIGN_SCHEMA,
    "envelope": ENVELOPE_SCHEMA,
    "error": ERROR_SCHEMA,
    "event": EVENT_SCHEMA,
    "expression": EXPRESSION_SCHEMA,
    "feedback-bundle": FEEDBACK_BUNDLE_SCHEMA,
    "invocation": INVOCATION_SCHEMA,
    "journal": JOURNAL_SCHEMA,
    "manifest": MANIFEST_SCHEMA,
    "matter": MATTER_SCHEMA,
    "observed-state": OBSERVED_STATE_SCHEMA,
    "operating": OPERATING_SCHEMA,
    "outcome": OUTCOME_SCHEMA,
    "project": PROJECT_SCHEMA,
    "quantum": QUANTUM_SCHEMA,
    "question": QUESTION_SCHEMA,
    "review-snapshot": REVIEW_SNAPSHOT_SCHEMA,
    "registry": REGISTRY_SCHEMA,
    "round": ROUND_SCHEMA,
    "semantic-commit-input": SEMANTIC_COMMIT_INPUT_SCHEMA,
    "specimen": SPECIMEN_SCHEMA,
    "synthesis": SYNTHESIS_SCHEMA,
    "valence-tool": VALENCE_TOOL_SCHEMA,
}


def names() -> list[str]:
    return sorted(SCHEMAS)


def get(name: str) -> dict:
    try:
        return deepcopy(SCHEMAS[name])
    except KeyError as exc:
        raise IsotopeError(
            "schema-not-found",
            f"Unknown schema: {name}",
            EXIT_NOT_FOUND,
            {"schema": name, "available": names()},
        ) from exc


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _invalid(path: str, rule: str, message: str) -> None:
    raise IsotopeError(
        "schema-invalid",
        message,
        EXIT_MALFORMED,
        {"path": path or "/", "rule": rule},
    )


def _validate(schema: dict, value: Any, path: str = "") -> None:
    if "const" in schema and value != schema["const"]:
        _invalid(path, "const", f"Value at {path or '/'} must equal {schema['const']!r}.")
    if "enum" in schema and value not in schema["enum"]:
        _invalid(path, "enum", f"Value at {path or '/'} is not an allowed value.")
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(item, value) for item in types):
            _invalid(path, "type", f"Value at {path or '/'} has the wrong type.")
        if value is None:
            return
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _invalid(path, "minLength", f"String at {path or '/'} must not be empty.")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            _invalid(path, "pattern", f"String at {path or '/'} has an invalid format.")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        _invalid(path, "minimum", f"Number at {path or '/'} is below the minimum.")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _invalid(path, "minItems", f"Array at {path or '/'} has too few items.")
        if schema.get("uniqueItems"):
            seen = set()
            for item in value:
                marker = repr(item)
                if marker in seen:
                    _invalid(path, "uniqueItems", f"Array at {path or '/'} contains duplicates.")
                seen.add(marker)
        for index, item in enumerate(value):
            _validate(schema.get("items", {}), item, f"{path}/{index}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                _invalid(f"{path}/{required}", "required", f"Required field {required!r} is missing.")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                _invalid(f"{path}/{extras[0]}", "additionalProperties", f"Unknown field {extras[0]!r}.")
        for key, item in value.items():
            if key in properties:
                _validate(properties[key], item, f"{path}/{key}")


def validate(entity: str, value: Any) -> None:
    _validate(get(entity), value)


def validate_quantum_payload(quantum_type: str, payload: Any) -> None:
    """Validate one quantum payload against its type's published $defs shape."""
    definitions = QUANTUM_SCHEMA["$defs"]
    key = f"{quantum_type}-payload"
    if key not in definitions:
        _invalid("/type", "enum", "Value at /type is not an allowed value.")
    _validate(definitions[key], payload, "/payload")
