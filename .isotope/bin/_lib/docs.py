"""Secure, marker-addressed retrieval from the human documentation corpus."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, IsotopeError
from .manifest import load as load_manifest
from .manifest import source as manifest_source
from .paths import ISOTOPE_DIR, Project


SECTION_ID = r"[a-z0-9][a-z0-9-]*"
MARKER = re.compile(
    rf"^[ \t]*<!-- isotope:section (?P<id>{SECTION_ID}):(?P<edge>start|end) -->[ \t]*\r?$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Section:
    section_id: str
    start: int
    end: int


def _relative(path: Path, project: Project) -> str:
    return path.relative_to(project.root).as_posix()


def resolve_doc_path(project: Project, requested: str) -> Path:
    raw = Path(requested)
    normalized_parts = requested.replace("\\", "/").split("/")
    if raw.is_absolute() or PureWindowsPath(requested).is_absolute() or ".." in normalized_parts:
        raise IsotopeError(
            "docs-path-refused",
            "Documentation paths must be project-relative and cannot contain '..'.",
            EXIT_REFUSED,
            {"path": requested},
        )
    if not raw.parts or normalized_parts[0] == ISOTOPE_DIR:
        raise IsotopeError(
            "docs-path-refused",
            "Documentation must live in the human corpus outside .isotope/.",
            EXIT_REFUSED,
            {"path": requested},
        )
    candidate = project.root.joinpath(raw)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise IsotopeError(
            "docs-file-not-found",
            "The documentation file does not exist.",
            EXIT_NOT_FOUND,
            {"path": requested},
        ) from exc
    except OSError as exc:
        raise IsotopeError(
            "docs-path-refused",
            "The documentation path could not be safely resolved.",
            EXIT_REFUSED,
            {"path": requested, "reason": str(exc)},
        ) from exc
    try:
        relative = resolved.relative_to(project.root)
    except ValueError as exc:
        raise IsotopeError(
            "docs-path-refused",
            "The documentation path escapes the project.",
            EXIT_REFUSED,
            {"path": requested},
        ) from exc
    if relative.parts and relative.parts[0] == ISOTOPE_DIR:
        raise IsotopeError(
            "docs-path-refused",
            "Documentation must live in the human corpus outside .isotope/.",
            EXIT_REFUSED,
            {"path": requested},
        )
    if not resolved.is_file():
        raise IsotopeError(
            "docs-file-not-found",
            "The documentation path is not a file.",
            EXIT_NOT_FOUND,
            {"path": requested},
        )
    return resolved


def read_text(project: Project, requested: str) -> tuple[Path, str, str]:
    path = resolve_doc_path(project, requested)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise IsotopeError(
            "docs-file-malformed",
            "The documentation file is not readable UTF-8.",
            EXIT_MALFORMED,
            {"path": requested, "reason": str(exc)},
        ) from exc
    return path, text, "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_sections(text: str, source_path: str) -> dict[str, Section]:
    sections: dict[str, Section] = {}
    opened: tuple[str, int] | None = None
    for match in MARKER.finditer(text):
        section_id = match.group("id")
        edge = match.group("edge")
        if edge == "start":
            if opened is not None:
                raise IsotopeError(
                    "docs-markers-invalid",
                    "Documentation sections cannot overlap or nest.",
                    EXIT_MALFORMED,
                    {"path": source_path, "section_id": section_id, "open_section": opened[0]},
                )
            if section_id in sections:
                raise IsotopeError(
                    "docs-markers-invalid",
                    "A documentation section ID appears more than once.",
                    EXIT_MALFORMED,
                    {"path": source_path, "section_id": section_id},
                )
            opened = (section_id, match.end())
        elif opened is None or opened[0] != section_id:
            raise IsotopeError(
                "docs-markers-invalid",
                "A documentation section marker is unbalanced or out of order.",
                EXIT_MALFORMED,
                {"path": source_path, "section_id": section_id},
            )
        else:
            sections[section_id] = Section(section_id, opened[1], match.start())
            opened = None
    if opened is not None:
        raise IsotopeError(
            "docs-markers-invalid",
            "A documentation section has no end marker.",
            EXIT_MALFORMED,
            {"path": source_path, "section_id": opened[0]},
        )
    return sections


def map_entries(project: Project) -> tuple[list[dict[str, str]], dict[str, str]]:
    manifest, manifest_revision = load_manifest(project)
    entries = manifest.get("docs", [])
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        concept = entry["concept"]
        if concept in seen:
            raise IsotopeError(
                "docs-map-invalid",
                "Each docs-map concept must be unique.",
                EXIT_MALFORMED,
                {"path": f"/docs/{index}/concept", "concept": concept},
            )
        seen.add(concept)
    return entries, manifest_source(manifest_revision)


def require_mapped_targets(project: Project, targets: list[dict[str, str]]) -> None:
    entries, _source = map_entries(project)
    mapped = {(item["concept"], item["path"], item["section_id"]) for item in entries}
    for index, target in enumerate(targets):
        identity = (target["concept"], target["path"], target["section_id"])
        if identity not in mapped:
            raise IsotopeError(
                "docs-target-refused",
                "An outcome documentation target is not present in the manifest docs map.",
                EXIT_REFUSED,
                {"path": f"/entity/doc_targets/{index}", "target": target},
            )


def section(project: Project, requested: str, section_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    path, text, file_revision = read_text(project, requested)
    relative = _relative(path, project)
    sections = parse_sections(text, relative)
    selected = sections.get(section_id)
    if selected is None:
        raise IsotopeError(
            "docs-section-not-found",
            "The requested documentation section does not exist.",
            EXIT_NOT_FOUND,
            {"path": relative, "section_id": section_id, "available": sorted(sections)},
        )
    content = text[selected.start:selected.end]
    if content.startswith("\r\n"):
        content = content[2:]
    elif content.startswith("\n"):
        content = content[1:]
    if content.endswith("\r\n"):
        content = content[:-2]
    elif content.endswith("\n"):
        content = content[:-1]
    return (
        {"path": relative, "section_id": section_id, "content": content},
        {"path": relative, "revision": file_revision},
    )


def validate_docs(project: Project) -> tuple[dict[str, Any], dict[str, str]]:
    entries, source = map_entries(project)
    parsed: dict[str, dict[str, Section] | None] = {}
    file_revisions: dict[str, str] = {}
    problems: list[dict[str, Any]] = []
    for entry in entries:
        requested = entry["path"]
        if requested not in parsed:
            try:
                path, text, file_revision = read_text(project, requested)
                relative = _relative(path, project)
                parsed[requested] = parse_sections(text, relative)
                file_revisions[relative] = file_revision
            except IsotopeError as exc:
                parsed[requested] = None
                problems.append({"code": exc.code, "message": exc.message, "details": exc.details})
        sections = parsed[requested]
        if sections is not None and entry["section_id"] not in sections:
            problems.append({
                "code": "docs-map-unresolved",
                "message": "A docs-map entry does not resolve to a marked section.",
                "details": {
                    "concept": entry["concept"],
                    "path": requested,
                    "section_id": entry["section_id"],
                },
            })
    if problems:
        raise IsotopeError(
            "docs-invalid",
            "The documentation corpus does not satisfy the docs map.",
            EXIT_MALFORMED,
            {"entries": len(entries), "problems": problems},
        )
    data = {
        "valid": True,
        "entries": len(entries),
        "files": len(parsed),
        "file_revisions": file_revisions,
    }
    return data, source
