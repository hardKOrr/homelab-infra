"""Valence: repo-local deterministic tools promoted from repeated evidence.

A tool is one ordinary Git-visible descriptor under
`.isotope/valence/<name>/tool.json`. It reaches the repository only through
human review and the normal commit flow; the CLI scaffolds, validates, and
runs it within repository authority and the host's containment. A suggestion
exists only when repeated `command` quanta cross the evidence threshold, and
every scaffolded descriptor cites the exact quanta it aggregates.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, EXIT_USAGE, IsotopeError
from .execution import run_bounded
from .paths import ISOTOPE_DIR, TOOLS_DIR, Project
from .revisions import load_json, revision, write_canonical
from .schemas import validate as validate_schema


SUGGEST_MIN_QUANTA = 3
SUGGEST_MIN_CONTEXTS = 2
OUTPUT_LIMIT = 20000

_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def tools_dir(project: Project) -> Path:
    return project.root / ISOTOPE_DIR / TOOLS_DIR


def tool_relative(name: str) -> str:
    return f"{ISOTOPE_DIR}/{TOOLS_DIR}/{name}/tool.json"


def tool_names(project: Project) -> list[str]:
    directory = tools_dir(project)
    if not directory.is_dir():
        return []
    return sorted(
        item.name for item in directory.iterdir() if (item / "tool.json").is_file()
    )


def _invalid(message: str, details: dict[str, Any]) -> IsotopeError:
    return IsotopeError("schema-invalid", message, EXIT_MALFORMED, details)


def _check_descriptor(name: str, value: dict[str, Any]) -> None:
    validate_schema("valence-tool", value)
    if value["name"] != name:
        raise _invalid("The tool name must match its directory.", {"path": "/name", "expected": name})
    parameters = value["input"]["parameters"]
    for key, description in parameters.items():
        if not _PARAMETER_NAME.fullmatch(key):
            raise _invalid("Tool parameter names are lower-case identifiers.", {"path": f"/input/parameters/{key}"})
        if not isinstance(description, str) or not description.strip():
            raise _invalid("Every tool parameter declares a non-empty description.", {"path": f"/input/parameters/{key}"})
    for index, element in enumerate(value["command"]["argv"]):
        for match in _PLACEHOLDER.finditer(element):
            if match.group(1) not in parameters:
                raise _invalid(
                    "Every command placeholder names a declared parameter.",
                    {"path": f"/command/argv/{index}", "placeholder": match.group(1)},
                )
    for index, element in enumerate(value["validation"]["argv"]):
        if _PLACEHOLDER.search(element):
            raise _invalid("The validation command takes no parameters.", {"path": f"/validation/argv/{index}"})
    if value["effect"] == "read-only" and value["authority"]:
        raise _invalid("A read-only tool declares no write authority.", {"path": "/authority"})
    if value["effect"] == "repo-write" and not value["authority"]:
        raise _invalid("A repo-write tool declares its exact write authority.", {"path": "/authority"})
    for index, prefix in enumerate(value["authority"]):
        path = PurePosixPath(prefix)
        if (
            path.is_absolute()
            or path.as_posix() != prefix
            or any(part in ("", ".", "..") for part in path.parts)
            or "\\" in prefix
            or ":" in prefix
        ):
            raise _invalid("Tool authority stays repo-relative.", {"path": f"/authority/{index}"})
        if prefix == ".git" or prefix.startswith(".git/") or prefix == ".isotope" or prefix.startswith(".isotope/"):
            raise _invalid(
                "Tool authority excludes Git control and Isotope state.",
                {"path": f"/authority/{index}"},
            )


def read_tool(project: Project, name: str) -> tuple[dict[str, Any], str]:
    path = tools_dir(project) / name / "tool.json"
    if not path.is_file():
        raise IsotopeError(
            "tool-not-found",
            f"No Valence tool {name!r} exists.",
            EXIT_NOT_FOUND,
            {"tool": name, "path": tool_relative(name)},
        )
    value, _ = load_json(path)
    _check_descriptor(name, value)
    return value, revision(value)


def scan(project: Project) -> list[dict[str, Any]]:
    from . import gitops

    states = []
    for name in tool_names(project):
        state: dict[str, Any] = {
            "name": name,
            "valid": True,
            "tracked": gitops.is_tracked(project, tool_relative(name)),
            "revision": None,
            "error": None,
        }
        try:
            _, state["revision"] = read_tool(project, name)
        except IsotopeError as exc:
            state["valid"] = False
            state["error"] = exc.code
        states.append(state)
    return states


def list_tools(project: Project) -> list[dict[str, Any]]:
    summaries = []
    for name in tool_names(project):
        try:
            value, _ = read_tool(project, name)
        except IsotopeError:
            continue
        summaries.append(
            {
                "name": value["name"],
                "description": value["description"],
                "effect": value["effect"],
                "parameters": value["input"]["parameters"],
                "evidence": value["evidence"],
            }
        )
    return summaries


def inspect(project: Project, name: str) -> dict[str, Any]:
    from . import gitops
    from .quanta import quantum_path

    value, tool_revision = read_tool(project, name)
    return {
        "tool": value,
        "revision": tool_revision,
        "tracked": gitops.is_tracked(project, tool_relative(name)),
        "evidence": [
            {"quantum": quantum_id, "resolved": quantum_path(project, quantum_id).is_file()}
            for quantum_id in value["evidence"]
        ],
    }


def _context(provenance: dict[str, Any]) -> str:
    if provenance.get("invocation") is not None:
        return f"invocation:{provenance['invocation']}"
    return f"specimen:{provenance['slug']}"


def signature_groups(project: Project) -> dict[str, dict[str, Any]]:
    from .quanta import normalize_command_signature, read_all

    groups: dict[str, dict[str, Any]] = {}
    for record in read_all(project):
        if record["type"] != "command":
            continue
        signature = normalize_command_signature(record["payload"]["signature"])
        group = groups.setdefault(signature, {"signature": signature, "quanta": [], "context_keys": set()})
        group["quanta"].append(record["id"])
        group["context_keys"].add(_context(record["provenance"]))
    for group in groups.values():
        group["count"] = len(group["quanta"])
        group["contexts"] = len(group.pop("context_keys"))
    return groups


def _meets_threshold(group: dict[str, Any]) -> bool:
    return group["count"] >= SUGGEST_MIN_QUANTA and group["contexts"] >= SUGGEST_MIN_CONTEXTS


def suggest(project: Project) -> dict[str, Any]:
    cited: set[str] = set()
    for name in tool_names(project):
        try:
            value, _ = read_tool(project, name)
        except IsotopeError:
            continue
        cited.update(value["evidence"])
    suggestions = [
        {"signature": group["signature"], "count": group["count"], "contexts": group["contexts"], "quanta": group["quanta"]}
        for group in signature_groups(project).values()
        if _meets_threshold(group) and not cited.intersection(group["quanta"])
    ]
    suggestions.sort(key=lambda item: item["signature"])
    return {
        "suggestions": suggestions,
        "thresholds": {"quanta": SUGGEST_MIN_QUANTA, "contexts": SUGGEST_MIN_CONTEXTS},
    }


def scaffold(project: Project, name: str, payload: Any) -> dict[str, Any]:
    from .quanta import normalize_command_signature

    expected = {"signature", "description", "effect", "authority", "parameters", "validation"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise IsotopeError(
            "invalid-input",
            "A tool scaffold requires exactly: signature, description, effect, authority, parameters, and validation.",
            EXIT_MALFORMED,
            {"path": "/"},
        )
    if (tools_dir(project) / name / "tool.json").exists():
        raise IsotopeError(
            "tool-exists",
            f"The Valence tool {name!r} already exists.",
            EXIT_CONFLICT,
            {"tool": name, "path": tool_relative(name)},
        )
    signature = normalize_command_signature(payload["signature"])
    group = signature_groups(project).get(signature)
    if group is None or not _meets_threshold(group):
        raise IsotopeError(
            "insufficient-evidence",
            "A tool suggestion requires repeated command evidence across distinct contexts.",
            EXIT_REFUSED,
            {
                "signature": signature,
                "count": 0 if group is None else group["count"],
                "contexts": 0 if group is None else group["contexts"],
                "thresholds": {"quanta": SUGGEST_MIN_QUANTA, "contexts": SUGGEST_MIN_CONTEXTS},
            },
        )
    argv = shlex.split(signature)
    descriptor = {
        "schema_version": "1",
        "name": name,
        "description": payload["description"],
        "evidence": group["quanta"],
        "input": {"parameters": payload["parameters"]},
        "command": {"argv": argv},
        "effect": payload["effect"],
        "authority": payload["authority"],
        "validation": payload["validation"],
    }
    _check_descriptor(name, descriptor)
    tool_revision = write_canonical(tools_dir(project) / name / "tool.json", descriptor)
    return {"tool": descriptor, "revision": tool_revision, "path": tool_relative(name)}


def _resolve_argv(value: dict[str, Any], assignments: dict[str, str]) -> list[str]:
    parameters = value["input"]["parameters"]
    undeclared = sorted(set(assignments) - set(parameters))
    if undeclared:
        raise IsotopeError(
            "invalid-input",
            "Every provided parameter must be declared by the tool.",
            EXIT_MALFORMED,
            {"undeclared": undeclared},
        )
    missing = sorted(set(parameters) - set(assignments))
    if missing:
        raise IsotopeError(
            "invalid-input",
            "Every declared parameter requires a value.",
            EXIT_MALFORMED,
            {"missing": missing},
        )
    return [
        _PLACEHOLDER.sub(lambda match: assignments[match.group(1)], element)
        for element in value["command"]["argv"]
    ]


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= OUTPUT_LIMIT:
        return text, False
    return text[:OUTPUT_LIMIT], True


def _execute(project: Project, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise IsotopeError("usage", "--timeout must be positive.", EXIT_USAGE)
    try:
        return run_bounded(argv, cwd=project.root, env=dict(os.environ), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise IsotopeError(
            "tool-timeout",
            "The tool did not terminate within its bound.",
            EXIT_CONFLICT,
            {"argv": argv, "timeout": timeout, "next_action": "raise --timeout or repair the command"},
        ) from exc
    except OSError as exc:
        raise IsotopeError(
            "tool-launch-failed",
            "The tool command could not be launched.",
            EXIT_NOT_FOUND,
            {"argv": argv, "reason": str(exc)},
        ) from exc


def _git_observation(project: Project, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project.root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise IsotopeError(
            "tool-authority-unavailable",
            "Valence could not observe repository authority.",
            EXIT_REFUSED,
            {"git_args": list(args), "stderr": completed.stderr.strip()},
        )
    return completed.stdout


def _require_reviewed(project: Project, name: str, value: dict[str, Any]) -> None:
    from . import gitops
    from .quanta import normalize_command_signature, read_quantum

    relative = tool_relative(name)
    reviewed = gitops.is_tracked(project, relative) and subprocess.run(
        ["git", "-C", str(project.root), "diff", "--quiet", "HEAD", "--", relative],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    if not reviewed:
        raise IsotopeError(
            "tool-unreviewed",
            "A Valence tool must match its committed reviewed descriptor before execution.",
            EXIT_REFUSED,
            {"tool": name, "path": relative, "next_action": "review and commit the descriptor"},
        )
    command_signature = normalize_command_signature(shlex.join(value["command"]["argv"]))
    for quantum_id in value["evidence"]:
        quantum = read_quantum(project, quantum_id)
        if (
            quantum["type"] != "command"
            or normalize_command_signature(quantum["payload"].get("signature", "")) != command_signature
        ):
            raise IsotopeError(
                "tool-evidence-invalid",
                "Every reviewed tool citation must resolve to its command signature.",
                EXIT_REFUSED,
                {"tool": name, "quantum": quantum_id, "signature": command_signature},
            )


def _entry_revision(path: Path) -> str:
    if path.is_symlink():
        return "link:" + hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
    if path.is_dir():
        return "dir"
    if not path.is_file():
        return "special:" + str(path.stat().st_mode)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return "file:" + digest.hexdigest()


def _repository_snapshot(project: Project) -> dict[str, Any]:
    paths: dict[str, str] = {}

    def visit(directory: Path, relative: PurePosixPath | None = None) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise IsotopeError(
                "tool-authority-unavailable",
                "Valence could not inspect the complete repository boundary.",
                EXIT_REFUSED,
                {"path": str(directory), "reason": str(exc)},
            ) from exc
        for entry in entries:
            if relative is None and entry.name == ".git":
                continue
            item_relative = PurePosixPath(entry.name) if relative is None else relative / entry.name
            item_path = Path(entry.path)
            key = item_relative.as_posix()
            try:
                paths[key] = _entry_revision(item_path)
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    visit(item_path, item_relative)
            except OSError as exc:
                raise IsotopeError(
                    "tool-authority-unavailable",
                    "Valence could not inspect the complete repository boundary.",
                    EXIT_REFUSED,
                    {"path": key, "reason": str(exc)},
                ) from exc

    visit(project.root)
    control = {
        "head": _git_observation(project, "rev-parse", "HEAD").strip(),
        "branch": _git_observation(project, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "index": _git_observation(project, "ls-files", "--stage", "-z"),
        "refs": _git_observation(project, "for-each-ref", "--format=%(refname)%00%(objectname)%00"),
        "config": _git_observation(project, "config", "--local", "--null", "--list"),
    }
    return {"paths": paths, "control": control}


def _authorized(relative: str, authority: list[str]) -> bool:
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix in authority)


def _remove_created_paths(project: Project, paths: list[str], before: dict[str, str]) -> list[str]:
    recovered: list[str] = []
    for relative in sorted(paths, key=lambda item: (item.count("/"), item), reverse=True):
        if relative in before:
            continue
        target = project.root / Path(*PurePosixPath(relative).parts)
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
                recovered.append(relative)
            elif target.is_dir():
                target.rmdir()
                recovered.append(relative)
        except OSError:
            continue
    return sorted(recovered)


def _execute_authorized(
    project: Project,
    name: str,
    value: dict[str, Any],
    argv: list[str],
    timeout: float,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    _require_reviewed(project, name, value)
    before = _repository_snapshot(project)
    execution_error: IsotopeError | None = None
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = _execute(project, argv, timeout)
    except IsotopeError as exc:
        execution_error = exc
    after = _repository_snapshot(project)
    changed = sorted(
        relative
        for relative in set(before["paths"]) | set(after["paths"])
        if before["paths"].get(relative) != after["paths"].get(relative)
    )
    authority = value["authority"] if value["effect"] == "repo-write" else []
    unauthorized = [relative for relative in changed if not _authorized(relative, authority)]
    control_changed = before["control"] != after["control"]
    if unauthorized or control_changed:
        recovered = _remove_created_paths(project, unauthorized, before["paths"])
        raise IsotopeError(
            "tool-authority-violated",
            "The tool changed repository state outside its reviewed authority.",
            EXIT_REFUSED,
            {
                "tool": name,
                "effect": value["effect"],
                "authority": authority,
                "paths": unauthorized,
                "git_control_changed": control_changed,
                "recovered_created_paths": recovered,
                "next_action": "inspect and restore any remaining unauthorized changes before retrying",
            },
        )
    if execution_error is not None:
        raise execution_error
    assert completed is not None
    return completed, changed


def run_tool(project: Project, name: str, assignments: dict[str, str], timeout: float) -> dict[str, Any]:
    from . import gitops

    value, _ = read_tool(project, name)
    argv = _resolve_argv(value, assignments)
    completed, changes = _execute_authorized(project, name, value, argv, timeout)
    stdout, stdout_truncated = _truncate(completed.stdout or "")
    stderr, stderr_truncated = _truncate(completed.stderr or "")
    return {
        "name": name,
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
        "tracked": gitops.is_tracked(project, tool_relative(name)),
        "changes": changes,
    }


def validate_tool(project: Project, name: str, timeout: float) -> dict[str, Any]:
    value, _ = read_tool(project, name)
    completed, changes = _execute_authorized(
        project, name, value, value["validation"]["argv"], timeout
    )
    stdout, _ = _truncate(completed.stdout or "")
    stderr, _ = _truncate(completed.stderr or "")
    return {
        "name": name,
        "passed": completed.returncode == value["validation"]["expect_exit"],
        "exit_code": completed.returncode,
        "expected": value["validation"]["expect_exit"],
        "stdout": stdout,
        "stderr": stderr,
        "changes": changes,
    }
