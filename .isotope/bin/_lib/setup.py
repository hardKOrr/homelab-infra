"""Composite distribution synchronization, inspection, and host observation."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, IsotopeError
from .journal import JournalWrite, run_transaction, transaction_scope
from .manifest import validate_value as validate_manifest
from .paths import ISOTOPE_DIR, MANIFEST_FILE, Project
from .revisions import bytes_revision, load_json, revision
from .schemas import VERSION_PATTERN, validate as validate_schema


REGISTRY_RELATIVE = f"{ISOTOPE_DIR}/registry.json"
MANIFEST_RELATIVE = f"{ISOTOPE_DIR}/{MANIFEST_FILE}"
SYNTHESIS_RELATIVE = f"{ISOTOPE_DIR}/synthesis.json"
LAUNCHER_RELATIVE = f"{ISOTOPE_DIR}/bin/isotope.py"
SUPPORTED_HOSTS = ("claude", "codex")
EXECUTABLE_REACTIONS = (
    "acceptance", "analyze", "construction", "decision", "design", "expression", "intake", "review",
)


def resource_root() -> Path:
    scripts = Path(__file__).resolve().parent.parent
    bundled = scripts / "_assets"
    return bundled if bundled.is_dir() else scripts.parent


def _load_resource(relative: str) -> tuple[Any, str]:
    path = resource_root() / relative
    value, raw = load_json(path)
    return value, bytes_revision(raw)


def build_catalog() -> dict[str, Any]:
    value, _ = _load_resource("build.json")
    source_version = value.get("source_version")
    if value.get("schema_version") != "1" or not isinstance(source_version, str) or not re.fullmatch(VERSION_PATTERN, source_version):
        raise IsotopeError("distribution-invalid", "The installed Isotope build catalog is invalid.", EXIT_MALFORMED)
    return value


def registry_scaffold() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "hosts": {
            host: {"enabled": False, "available": None, "default_model": None, "models": []}
            for host in SUPPORTED_HOSTS
        },
    }


def initialize(project: Project, value: Any) -> dict[str, Any]:
    """Create the consumer manifest through one retry-safe semantic operation."""
    validate_manifest(value)
    desired_revision = revision(value)
    manifest_path = project.root / MANIFEST_RELATIVE
    with transaction_scope(project):
        if manifest_path.is_file():
            existing, _ = load_json(manifest_path)
            validate_manifest(existing)
            existing_revision = revision(existing)
            if existing_revision != desired_revision:
                raise IsotopeError(
                    "manifest-conflict",
                    "The repository already has a different Isotope manifest.",
                    EXIT_CONFLICT,
                    {"path": MANIFEST_RELATIVE, "revision": existing_revision},
                )
            status = "already-initialized"
        else:
            run_transaction(
                project,
                journal_type="setup",
                operation="setup.init",
                writes=[JournalWrite(MANIFEST_RELATIVE, None, desired_revision, value)],
            )
            status = "initialized"
    return {"status": status, "path": MANIFEST_RELATIVE, "revision": desired_revision}


def validate_registry(value: Any) -> None:
    validate_schema("registry", value)
    for host, entry in value["hosts"].items():
        if host not in SUPPORTED_HOSTS:
            raise IsotopeError("schema-invalid", "The registry names an unsupported host.", EXIT_MALFORMED, {"path": f"/hosts/{host}"})
        ids: set[str] = set()
        for index, model in enumerate(entry["models"]):
            model_id = model["id"]
            if model_id in ids:
                raise IsotopeError("schema-invalid", "Registry model IDs must be unique per host.", EXIT_MALFORMED, {"path": f"/hosts/{host}/models/{index}/id"})
            ids.add(model_id)
            unknown = sorted(set(model["reactions"]) - set(EXECUTABLE_REACTIONS))
            if unknown:
                raise IsotopeError(
                    "schema-invalid",
                    "Registry model reactions must name executable package reactions.",
                    EXIT_MALFORMED,
                    {"path": f"/hosts/{host}/models/{index}/reactions", "unknown": unknown},
                )
        default = entry["default_model"]
        if default is not None and default not in ids:
            raise IsotopeError("schema-invalid", "default_model must name a declared model.", EXIT_MALFORMED, {"path": f"/hosts/{host}/default_model"})


def load_registry(project: Project) -> tuple[dict[str, Any], str]:
    path = project.root / REGISTRY_RELATIVE
    if not path.is_file():
        raise IsotopeError("registry-missing", "The consumer registry is missing; run isotope setup sync.", EXIT_NOT_FOUND, {"path": REGISTRY_RELATIVE})
    value, _ = load_json(path)
    validate_registry(value)
    return value, revision(value)


def _require_registry_host(registry: dict[str, Any], host: str) -> dict[str, Any]:
    if host not in SUPPORTED_HOSTS:
        raise IsotopeError(
            "registry-host-unsupported",
            "The installed Isotope distribution does not implement the requested host.",
            EXIT_REFUSED,
            {"host": host, "supported": list(SUPPORTED_HOSTS)},
        )
    return registry["hosts"][host]


def _write_registry(project: Project, prior: str, updated: dict[str, Any], operation: str) -> str:
    validate_registry(updated)
    new = revision(updated)
    if new != prior:
        run_transaction(
            project,
            journal_type="setup",
            operation=operation,
            writes=[JournalWrite(REGISTRY_RELATIVE, prior, new, updated)],
        )
    return new


def set_registry_host(project: Project, host: str, *, enabled: bool) -> dict[str, Any]:
    """Enable or disable one implemented host without replacing consumer model choices."""
    with transaction_scope(project):
        registry, prior = load_registry(project)
        entry = _require_registry_host(registry, host)
        changed = entry["enabled"] != enabled
        updated = deepcopy(registry)
        updated["hosts"][host]["enabled"] = enabled
        new = _write_registry(project, prior, updated, f"registry.host.{'enable' if enabled else 'disable'}")
    return {
        "host": host,
        "enabled": enabled,
        "outcome": "enabled" if enabled else "disabled",
        "changed": changed,
        "revision": new,
    }


def add_registry_model(project: Project, host: str, model: Any) -> dict[str, Any]:
    """Add one exact model option; an identical existing declaration is idempotent."""
    if not isinstance(model, dict):
        raise IsotopeError("invalid-input", "A registry model must be a JSON object.", EXIT_MALFORMED, {"path": "/"})
    with transaction_scope(project):
        registry, prior = load_registry(project)
        entry = _require_registry_host(registry, host)
        model_id = model.get("id")
        existing = next((item for item in entry["models"] if item.get("id") == model_id), None)
        if existing is not None:
            if existing != model:
                raise IsotopeError(
                    "registry-model-conflict",
                    "The model identity already carries a different registry declaration.",
                    EXIT_CONFLICT,
                    {"host": host, "model": model_id},
                )
            return {"host": host, "model": model_id, "outcome": "existing", "changed": False, "revision": prior}
        updated = deepcopy(registry)
        updated_entry = updated["hosts"][host]
        updated_entry["models"].append(deepcopy(model))
        if updated_entry["default_model"] is None:
            updated_entry["default_model"] = model_id
        new = _write_registry(project, prior, updated, "registry.model.add")
    return {"host": host, "model": model_id, "outcome": "added", "changed": True, "revision": new}


def remove_registry_model(project: Project, host: str, model_id: str) -> dict[str, Any]:
    """Remove one model option; an absent identity is an equivalent completion."""
    with transaction_scope(project):
        registry, prior = load_registry(project)
        entry = _require_registry_host(registry, host)
        existing = next((item for item in entry["models"] if item["id"] == model_id), None)
        if existing is None:
            return {"host": host, "model": model_id, "outcome": "absent", "changed": False, "revision": prior}
        updated = deepcopy(registry)
        updated_entry = updated["hosts"][host]
        updated_entry["models"] = [item for item in updated_entry["models"] if item["id"] != model_id]
        if updated_entry["default_model"] == model_id:
            updated_entry["default_model"] = None
        new = _write_registry(project, prior, updated, "registry.model.remove")
    return {"host": host, "model": model_id, "outcome": "removed", "changed": True, "revision": new}


def _resource_files() -> dict[str, str]:
    root = resource_root()
    result: dict[str, str] = {}
    scripts = Path(__file__).resolve().parent.parent
    for path in scripts.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc" or "_assets" in path.parts:
            continue
        result[f"{ISOTOPE_DIR}/bin/{path.relative_to(scripts).as_posix()}"] = path.read_text(encoding="utf-8")
    for name in ("build.json", "agents", "skills", "reactions", "schemas", "targets"):
        source = root / name
        candidates = [source] if source.is_file() else sorted(item for item in source.rglob("*") if item.is_file())
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            result[f"{ISOTOPE_DIR}/bin/_assets/{relative}"] = path.read_text(encoding="utf-8")
    return result


def _render(template: str, nucleus: str, source_version: str) -> str:
    return (
        template.replace("{{NUCLEUS}}", nucleus.rstrip())
        .replace("{{SOURCE_VERSION}}", source_version)
        .replace("{{LAUNCHER}}", LAUNCHER_RELATIVE)
    ).rstrip() + "\n"


def _generated_files(catalog: dict[str, Any]) -> dict[str, tuple[str, str, str | None]]:
    root = resource_root()
    files = {path: (text, "shared-cli", None) for path, text in _resource_files().items()}
    review_nucleus = (root / "agents" / "review" / "nucleus.md").read_text(encoding="utf-8")
    acceptance_nucleus = (root / "agents" / "acceptance" / "nucleus.md").read_text(encoding="utf-8")
    analyze_nucleus = (root / "agents" / "analyze" / "nucleus.md").read_text(encoding="utf-8")
    construction_nucleus = (root / "agents" / "construction" / "nucleus.md").read_text(encoding="utf-8")
    decision_nucleus = (root / "agents" / "decision" / "nucleus.md").read_text(encoding="utf-8")
    design_nucleus = (root / "agents" / "design" / "nucleus.md").read_text(encoding="utf-8")
    expression_nucleus = (root / "agents" / "expression" / "nucleus.md").read_text(encoding="utf-8")
    intake_nucleus = (root / "agents" / "intake" / "nucleus.md").read_text(encoding="utf-8")
    architect_nucleus = (root / "skills" / "architect" / "nucleus.md").read_text(encoding="utf-8")
    operate_nucleus = (root / "skills" / "operate" / "nucleus.md").read_text(encoding="utf-8")
    for host in ("claude", "codex"):
        target = root / "targets" / host
        renderer, _ = load_json(target / "renderer.json")
        acceptance_template = (target / renderer["acceptance_template"]).read_text(encoding="utf-8")
        analyze_template = (target / renderer["analyze_template"]).read_text(encoding="utf-8")
        review_template = (target / renderer["review_template"]).read_text(encoding="utf-8")
        construction_template = (target / renderer["construction_template"]).read_text(encoding="utf-8")
        decision_template = (target / renderer["decision_template"]).read_text(encoding="utf-8")
        design_template = (target / renderer["design_template"]).read_text(encoding="utf-8")
        expression_template = (target / renderer["expression_template"]).read_text(encoding="utf-8")
        intake_template = (target / renderer["intake_template"]).read_text(encoding="utf-8")
        architect_template = (target / renderer["architect_template"]).read_text(encoding="utf-8")
        operate_template = (target / renderer["operate_template"]).read_text(encoding="utf-8")
        files[renderer["acceptance_destination"]] = (
            _render(acceptance_template, acceptance_nucleus, catalog["source_version"]), "acceptance-agent", host
        )
        files[renderer["analyze_destination"]] = (
            _render(analyze_template, analyze_nucleus, catalog["source_version"]), "analyze-agent", host
        )
        files[renderer["review_destination"]] = (
            _render(review_template, review_nucleus, catalog["source_version"]), "review-agent", host
        )
        files[renderer["construction_destination"]] = (
            _render(construction_template, construction_nucleus, catalog["source_version"]), "construction-agent", host
        )
        files[renderer["decision_destination"]] = (
            _render(decision_template, decision_nucleus, catalog["source_version"]), "decision-agent", host
        )
        files[renderer["design_destination"]] = (
            _render(design_template, design_nucleus, catalog["source_version"]), "design-agent", host
        )
        files[renderer["expression_destination"]] = (
            _render(expression_template, expression_nucleus, catalog["source_version"]), "expression-agent", host
        )
        files[renderer["intake_destination"]] = (
            _render(intake_template, intake_nucleus, catalog["source_version"]), "intake-agent", host
        )
        files[renderer["architect_destination"]] = (
            _render(architect_template, architect_nucleus, catalog["source_version"]), "architect-owner", host
        )
        files[renderer["operate_destination"]] = (
            _render(operate_template, operate_nucleus, catalog["source_version"]), "operate-owner", host
        )
    return files


def _synthesis(catalog: dict[str, Any], files: dict[str, tuple[str, str, str | None]]) -> dict[str, Any]:
    targets = {}
    for host, entry in catalog["targets"].items():
        target_root = resource_root() / "targets" / host
        targets[host] = {
            "renderer_version": entry["renderer_version"],
            "adapter_version": entry["adapter_version"],
            "renderer_revision": bytes_revision((target_root / "renderer.json").read_bytes()),
            "adapter_revision": bytes_revision((target_root / "launch.json").read_bytes()),
        }
    return {
        "schema_version": "1",
        "source_version": catalog["source_version"],
        "targets": targets,
        "files": [
            {
                "path": path,
                "revision": bytes_revision(text.encode("utf-8")),
                "kind": kind,
                "host": host,
            }
            for path, (text, kind, host) in sorted(files.items())
        ],
        "observations": {},
    }


def sync(project: Project) -> dict[str, Any]:
    catalog = build_catalog()
    generated = _generated_files(catalog)
    synthesis = _synthesis(catalog, generated)
    with transaction_scope(project):
        if (project.root / ISOTOPE_DIR / "operating.json").exists():
            raise IsotopeError("lifecycle-refused", "Setup synchronization requires no armed operation.", EXIT_REFUSED, {"next_action": "run isotope run teardown first"})
        registry_path = project.root / REGISTRY_RELATIVE
        if registry_path.is_file():
            registry, _ = load_json(registry_path)
            validate_registry(registry)
        else:
            registry = registry_scaffold()
        old_paths: set[str] = set()
        synthesis_path = project.root / SYNTHESIS_RELATIVE
        if synthesis_path.is_file():
            old, _ = load_json(synthesis_path)
            if isinstance(old, dict):
                old_paths = {item.get("path") for item in old.get("files", []) if isinstance(item, dict) and isinstance(item.get("path"), str)}
        writes: list[JournalWrite] = []
        for path, (text, _, _) in sorted(generated.items()):
            target = project.root / path
            prior = bytes_revision(target.read_bytes()) if target.is_file() else None
            new = bytes_revision(text.encode("utf-8"))
            if prior != new:
                writes.append(JournalWrite(path, prior, new, text, "text"))
        for path in sorted(old_paths - set(generated)):
            target = project.root / path
            if target.is_file():
                writes.append(JournalWrite(path, bytes_revision(target.read_bytes()), None, format="text"))
        if not registry_path.is_file():
            writes.append(JournalWrite(REGISTRY_RELATIVE, None, revision(registry), registry))
        prior_synthesis = None
        if synthesis_path.is_file():
            old, _ = load_json(synthesis_path)
            prior_synthesis = revision(old)
        if prior_synthesis != revision(synthesis):
            writes.append(JournalWrite(SYNTHESIS_RELATIVE, prior_synthesis, revision(synthesis), synthesis))
        if writes:
            run_transaction(project, journal_type="setup", operation="setup.sync", writes=writes)
    return {"status": "synced", "source_version": catalog["source_version"], "files": len(generated), "observations_required": sorted(catalog["targets"])}


def load_synthesis(project: Project) -> tuple[dict[str, Any], str]:
    path = project.root / SYNTHESIS_RELATIVE
    if not path.is_file():
        raise IsotopeError("synthesis-missing", "Generated-asset synthesis is missing; run isotope setup sync.", EXIT_NOT_FOUND, {"path": SYNTHESIS_RELATIVE})
    value, _ = load_json(path)
    validate_schema("synthesis", value)
    return value, revision(value)


def _version(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)


def inspect(project: Project) -> dict[str, Any]:
    catalog = build_catalog()
    try:
        synthesis, synthesis_revision = load_synthesis(project)
    except IsotopeError as exc:
        if exc.code != "synthesis-missing":
            raise
        return {"state": "stale", "diagnosis": "synthesis-stale", "next_action": "run isotope setup sync", "source_version": catalog["source_version"], "synthesis_version": None, "files": []}
    distribution_version = catalog["source_version"]
    consumer_version = synthesis["source_version"]
    if _version(distribution_version) < _version(consumer_version):
        return {"state": "stale", "diagnosis": "distribution-stale", "next_action": "update the installed Isotope distribution", "source_version": distribution_version, "synthesis_version": consumer_version, "files": []}
    if _version(distribution_version) > _version(consumer_version):
        return {"state": "stale", "diagnosis": "synthesis-stale", "next_action": "run isotope setup sync", "source_version": distribution_version, "synthesis_version": consumer_version, "files": []}
    drift = []
    for item in synthesis["files"]:
        path = project.root / item["path"]
        observed = bytes_revision(path.read_bytes()) if path.is_file() else None
        if observed != item["revision"]:
            drift.append({"path": item["path"], "expected": item["revision"], "observed": observed})
    return {
        "state": "ready" if not drift else "stale",
        "diagnosis": None if not drift else "synthesis-stale",
        "next_action": None if not drift else "run isotope setup sync",
        "source_version": distribution_version,
        "synthesis_version": consumer_version,
        "synthesis_revision": synthesis_revision,
        "files": drift,
        "observed_hosts": sorted(synthesis["observations"]),
    }


def observe(project: Project, host: str, source_version: str, adapter_version: str) -> dict[str, Any]:
    if host not in ("claude", "codex"):
        raise IsotopeError("usage", "--host must be claude or codex.", EXIT_MALFORMED)
    if os.environ.get("ISOTOPE_HOST") != host:
        raise IsotopeError(
            "observation-unauthorized",
            "setup observe must be called by the matching active host adapter.",
            EXIT_REFUSED,
            {"host": host, "next_action": "start the matching host or run its SessionStart adapter"},
        )
    diagnosis = inspect(project)
    if diagnosis["state"] != "ready":
        raise IsotopeError(diagnosis["diagnosis"], "Generated assets are not ready for observation.", EXIT_CONFLICT, diagnosis)
    catalog = build_catalog()
    expected_adapter = catalog["targets"][host]["adapter_version"]
    if source_version != catalog["source_version"] or adapter_version != expected_adapter:
        raise IsotopeError("observation-mismatch", "The host observation versions do not match the installed distribution.", EXIT_CONFLICT, {"host": host, "source_version": source_version, "adapter_version": adapter_version})
    with transaction_scope(project):
        synthesis, prior = load_synthesis(project)
        updated = deepcopy(synthesis)
        updated["observations"][host] = {
            "source_version": source_version,
            "adapter_version": adapter_version,
            "asset_revisions": {
                item["path"]: item["revision"]
                for item in synthesis["files"]
                if item["host"] in (None, host)
            },
        }
        validate_schema("synthesis", updated)
        if revision(updated) != prior:
            run_transaction(project, journal_type="setup", operation="setup.observe", writes=[JournalWrite(SYNTHESIS_RELATIVE, prior, revision(updated), updated)])
    return {"status": "observed", "host": host, "source_version": source_version, "adapter_version": adapter_version}


def require_ready(project: Project, host: str) -> tuple[dict[str, Any], str]:
    diagnosis = inspect(project)
    if diagnosis["state"] != "ready":
        raise IsotopeError(diagnosis["diagnosis"], "Generated assets are not ready.", EXIT_CONFLICT, diagnosis)
    synthesis, synthesis_revision = load_synthesis(project)
    if host not in synthesis["observations"]:
        raise IsotopeError("host-unobserved", "The active host has not observed these generated assets.", EXIT_REFUSED, {"host": host, "next_action": f"run isotope setup observe --host {host} --source-version {synthesis['source_version']} --adapter-version {synthesis['targets'][host]['adapter_version']}"})
    return synthesis, synthesis_revision
