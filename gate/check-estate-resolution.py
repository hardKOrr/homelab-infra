#!/usr/bin/env python3
"""Require estate-scoped application playbooks to resolve their estate.

The estate overlay is tested separately in 'test-config-loading.sh'. This check covers
the other half of that contract: a playbook that consumes estate-scoped routing or
identity data must execute 'tasks/resolve-estate.yml' in the playbook itself. It uses
the parsed YAML tree rather than grep so comments cannot satisfy the check.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterator

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


REPO = Path(__file__).resolve().parent.parent
PLAYBOOKS = Path(
    os.environ.get("GATE_APP_PLAYBOOKS_DIR", REPO / "ansible" / "playbooks" / "apps")
)
CATALOG = Path(
    os.environ.get("GATE_APPLICATIONS_CATALOG", REPO / "catalog" / "applications.yml")
)
RESOLVE_ESTATES = "tasks/resolve-estate.yml"
INCLUDE_KEYS = {"include_tasks", "import_tasks"}
WIRING_PATH = re.compile(r"(?:^|/)tasks/(?:un)?wiring(?:/|$)")
INFRA_REFERENCE = re.compile(r"homelabinfra_infra\.(domain|sso)")


def _scalar_text(node: Node) -> str:
    """Return all scalar text below a YAML node for path matching."""

    if isinstance(node, ScalarNode):
        return node.value
    if isinstance(node, SequenceNode):
        return "\n".join(_scalar_text(child) for child in node.value)
    if isinstance(node, MappingNode):
        return "\n".join(
            f"{_scalar_text(key)}\n{_scalar_text(value)}" for key, value in node.value
        )
    return ""


def _mappings(node: Node) -> Iterator[tuple[ScalarNode, Node]]:
    """Yield every mapping pair in a composed YAML document."""

    if isinstance(node, MappingNode):
        for key, value in node.value:
            if isinstance(key, ScalarNode):
                yield key, value
            yield from _mappings(value)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _mappings(child)


def _scalars(node: Node) -> Iterator[ScalarNode]:
    if isinstance(node, ScalarNode):
        yield node
    elif isinstance(node, MappingNode):
        for key, value in node.value:
            yield from _scalars(key)
            yield from _scalars(value)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _scalars(child)


def _load_documents(path: Path) -> list[Node]:
    with path.open() as stream:
        return [document for document in yaml.compose_all(stream) if document is not None]


def _estate_resolution_lines(documents: list[Node]) -> list[int]:
    lines: list[int] = []
    for document in documents:
        for key, value in _mappings(document):
            if key.value.rsplit(".", 1)[-1] not in INCLUDE_KEYS:
                continue
            if RESOLVE_ESTATES in _scalar_text(value):
                lines.append(key.start_mark.line)
    return lines


def _wiring_lines(documents: list[Node]) -> list[int]:
    """Return source lines that introduce external wiring or its inputs."""

    lines: list[int] = []
    for document in documents:
        for key, value in _mappings(document):
            key_name = key.value.rsplit(".", 1)[-1]
            if key_name.startswith("wiring_"):
                lines.append(key.start_mark.line)
            elif key_name in INCLUDE_KEYS and WIRING_PATH.search(_scalar_text(value)):
                lines.append(key.start_mark.line)
            elif key_name in {"include_role", "import_role"} and re.search(
                r"(?:^|/)(?:un)?wiring(?:/|$)", _scalar_text(value)
            ):
                lines.append(key.start_mark.line)
    return lines


def _references_estate_infra(documents: list[Node]) -> set[str]:
    references: set[str] = set()
    for document in documents:
        for scalar in _scalars(document):
            references.update(INFRA_REFERENCE.findall(scalar.value))
    return references


def _catalog_scopes() -> dict[str, str]:
    with CATALOG.open() as stream:
        catalog = yaml.safe_load(stream) or {}
    applications = catalog.get("applications")
    if not isinstance(applications, dict):
        raise ValueError("catalog/applications.yml has no applications mapping")
    return {
        slug: details.get("scope")
        for slug, details in applications.items()
        if isinstance(details, dict)
    }


def main() -> int:
    findings: list[str] = []

    try:
        scopes = _catalog_scopes()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"check-estate-resolution: cannot load {CATALOG}: {exc}", file=sys.stderr)
        return 1

    playbooks = sorted(PLAYBOOKS.glob("*.yml"))
    if not playbooks:
        print(
            f"check-estate-resolution: no application playbooks found in {PLAYBOOKS}",
            file=sys.stderr,
        )
        return 1

    for playbook in playbooks:
        try:
            documents = _load_documents(playbook)
        except (OSError, yaml.YAMLError) as exc:
            findings.append(f"{playbook}: could not parse YAML ({exc})")
            continue

        wiring_lines = _wiring_lines(documents)
        wiring = bool(wiring_lines)
        references = _references_estate_infra(documents)
        scope = scopes.get(playbook.stem)

        reasons: list[str] = []
        if wiring and references:
            reasons.append(
                "uses " + ", ".join(f"homelabinfra_infra.{ref}" for ref in sorted(references))
            )
        if scope == "estate" and wiring:
            reasons.append("catalog scope is estate and the playbook wires external services")

        resolution_lines = _estate_resolution_lines(documents)
        resolves_before_wiring = resolution_lines and (
            not wiring_lines or min(resolution_lines) < min(wiring_lines)
        )
        if reasons and not resolves_before_wiring:
            findings.append(
                f"{playbook}: {'; '.join(reasons)}; "
                f"include {RESOLVE_ESTATES} before wiring"
            )

    if findings:
        print("check-estate-resolution: estate wiring violation(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    print(
        f"check-estate-resolution: {len(playbooks)} application playbook(s) scanned, "
        "0 problem(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
