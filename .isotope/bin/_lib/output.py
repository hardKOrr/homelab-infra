"""Versioned output envelopes and terminal projections."""

from __future__ import annotations

import json
from typing import Any

from .errors import IsotopeError
from .paths import Project


SCHEMA_VERSION = "1"


def envelope(
    operation: str,
    status: str,
    *,
    project: Project | None = None,
    source: dict[str, Any] | None = None,
    data: Any = None,
    page: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "status": status,
        "project": project.as_dict() if project else None,
        "source": source,
        "data": data,
        "page": page,
    }
    if error is not None:
        result["error"] = error
    return result


def error_envelope(
    operation: str, exc: IsotopeError, project: Project | None = None
) -> dict[str, Any]:
    return envelope(
        operation,
        "error",
        project=project,
        error={"code": exc.code, "message": exc.message, "details": exc.details},
    )


def render(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload["status"] == "error":
        err = payload["error"]
        return f"error [{err['code']}]: {err['message']}"
    data = payload.get("data")
    if isinstance(data, dict):
        if "text" in data:
            return str(data["text"])
        if payload.get("operation") == "docs.section" and "content" in data:
            return str(data["content"])
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
