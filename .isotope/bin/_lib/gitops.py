"""Bounded semantic Git operations: queries, arm-time branch handling, snapshots.

Every operation here serves a declared lifecycle intent; arbitrary Git
passthrough stays outside Isotope. The Review snapshot canonicalizes every
Git-visible input Review judges — base, HEAD, index, tracked worktree
modifications and deletions, and untracked paths — excluding only
Isotope-managed lifecycle state.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import PurePosixPath
from typing import Any

from .errors import EXIT_CONFLICT, EXIT_MALFORMED, EXIT_NOT_FOUND, EXIT_REFUSED, IsotopeError
from .paths import MANAGED_FILES, MANAGED_PREFIXES, Project, is_managed_path
from .revisions import revision
from .schemas import validate as validate_schema


def _git(
    project: Project,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    process_env = None
    if env is not None:
        process_env = os.environ.copy()
        process_env.update(env)
    result = subprocess.run(
        ["git", "-C", str(project.root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
        env=process_env,
    )
    if check and result.returncode != 0:
        raise IsotopeError(
            "git-failed",
            f"git {args[0]} failed.",
            EXIT_CONFLICT,
            {"args": list(args), "stderr": result.stderr.strip()},
        )
    return result


def resolve_commit(project: Project, ref: str) -> str:
    result = _git(project, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        raise IsotopeError(
            "ref-not-found",
            f"The ref {ref!r} does not resolve to a commit.",
            EXIT_NOT_FOUND,
            {"ref": ref},
        )
    return commit


def head_commit(project: Project) -> str:
    return resolve_commit(project, "HEAD")


def current_branch(project: Project) -> str | None:
    result = _git(project, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else None


def branch_exists(project: Project, name: str) -> bool:
    result = _git(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False)
    return result.returncode == 0


def create_branch(project: Project, name: str, commit: str) -> None:
    _git(project, "branch", name, commit)


def switch_branch(project: Project, name: str) -> None:
    _git(project, "switch", name)


def branch_commit(project: Project, name: str) -> str:
    if not branch_exists(project, name):
        raise IsotopeError("branch-not-found", "The requested local branch does not exist.", EXIT_NOT_FOUND, {"branch": name})
    return resolve_commit(project, f"refs/heads/{name}")


def status_entries(project: Project) -> list[dict[str, str]]:
    """Parsed `status --porcelain=v1 -z -uall` records: {x, y, path, orig_path}."""
    result = _git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    fields = result.stdout.split("\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        entry = {"x": record[0], "y": record[1], "path": record[3:], "orig_path": ""}
        if entry["x"] in ("R", "C") and index < len(fields):
            entry["orig_path"] = fields[index]
            index += 1
        entries.append(entry)
    return entries


def baseline_violations(project: Project) -> list[str]:
    """Git-visible changes outside Isotope-managed state; empty means clean."""
    dirty = []
    for entry in status_entries(project):
        paths = [entry["path"]] + ([entry["orig_path"]] if entry["orig_path"] else [])
        if all(is_managed_path(path) for path in paths):
            continue
        dirty.append(entry["path"])
    return sorted(dirty)


def changed_paths(project: Project, *, include_managed: bool = False) -> list[str]:
    """Git-visible changed paths, excluding managed lifecycle state by default."""
    paths: set[str] = set()
    for entry in status_entries(project):
        for path in (entry["path"], entry["orig_path"]):
            if path and (include_managed or not is_managed_path(path)):
                paths.add(path)
    return sorted(paths)


def deployment_paths(project: Project) -> list[str]:
    """Exact durable lifecycle paths that deploy must carry into repository history."""
    transient = set(MANAGED_FILES)
    return sorted(
        path
        for path in changed_paths(project, include_managed=True)
        if is_managed_path(path) and path not in transient
    )


def index_paths(project: Project) -> list[str]:
    result = _git(project, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACDMRTUXB")
    return sorted(path for path in result.stdout.split("\0") if path)


def is_tracked(project: Project, path: str) -> bool:
    return _git(project, "ls-files", "--error-unmatch", "--", path, check=False).returncode == 0


def _normalized_paths(files: Any, *, allow_managed: bool) -> list[str]:
    if not isinstance(files, list) or not files:
        raise IsotopeError("invalid-input", "files must be a non-empty array.", EXIT_MALFORMED, {"path": "/files"})
    normalized: list[str] = []
    for index, value in enumerate(files):
        if not isinstance(value, str) or not value or "\\" in value:
            raise IsotopeError("invalid-input", "Every file must be a normalized repo-relative POSIX path.", EXIT_MALFORMED, {"path": f"/files/{index}"})
        path = PurePosixPath(value)
        if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
            raise IsotopeError("invalid-input", "Every file must be a normalized repo-relative POSIX path.", EXIT_MALFORMED, {"path": f"/files/{index}"})
        if not allow_managed and is_managed_path(value):
            raise IsotopeError("protected-effect", "Semantic commit does not accept managed lifecycle paths.", EXIT_REFUSED, {"path": value})
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise IsotopeError("invalid-input", "files must not contain duplicates.", EXIT_MALFORMED, {"path": "/files"})
    return sorted(normalized)


def commit_paths(project: Project, commit: str) -> list[str]:
    result = _git(project, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit)
    return sorted(path for path in result.stdout.split("\0") if path)


def commit_subject(project: Project, commit: str) -> str:
    return _git(project, "show", "-s", "--format=%s", commit).stdout.rstrip("\r\n")


def commit_parent(project: Project, commit: str) -> str | None:
    result = _git(project, "rev-parse", "--verify", "--quiet", f"{commit}^", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def unstage(project: Project, files: list[str]) -> None:
    if files:
        _git(project, "restore", "--staged", "--", *files)


def semantic_commit(
    project: Project,
    *,
    expected_head: str,
    files: Any,
    reason: str,
    allow_managed: bool = False,
    force_add: bool = False,
) -> dict[str, Any]:
    """Commit exactly declared paths, with retry recognition across Git crash seams."""
    declared = _normalized_paths(files, allow_managed=allow_managed)
    if not isinstance(expected_head, str) or len(expected_head) != 40 or any(ch not in "0123456789abcdef" for ch in expected_head):
        raise IsotopeError("invalid-input", "expected_head must be a full lowercase commit id.", EXIT_MALFORMED, {"path": "/expected_head"})
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip() or "\n" in reason or "\r" in reason:
        raise IsotopeError("invalid-input", "reason must be one non-empty canonical subject line.", EXIT_MALFORMED, {"path": "/reason"})
    current = head_commit(project)
    if current != expected_head:
        if commit_parent(project, current) == expected_head and commit_subject(project, current) == reason:
            committed = commit_paths(project, current)
            extras = set(committed) - set(declared)
            # A managed-path retry recomputes its declared set from the now-clean
            # worktree, so the child is equivalent when every extra committed path
            # is durable managed lifecycle state.
            equivalent = set(declared) <= set(committed) and (
                not extras
                or (allow_managed and all(is_managed_path(path) and path not in MANAGED_FILES for path in extras))
            )
            if equivalent:
                return {"status": "already-committed", "commit": current, "files": committed, "reason": reason}
        raise IsotopeError("head-moved", "HEAD moved beyond the commit input's expected revision.", EXIT_CONFLICT, {"expected": expected_head, "actual": current})
    staged = index_paths(project)
    outside = sorted(set(staged) - set(declared))
    if outside:
        raise IsotopeError("index-occupied", "The index contains paths outside this semantic commit.", EXIT_CONFLICT, {"paths": outside})
    changed = set(changed_paths(project, include_managed=allow_managed))
    missing = sorted(set(declared) - changed - set(staged))
    if force_add:
        missing = [path for path in missing if not (project.root / path).is_file()]
    if missing:
        raise IsotopeError("path-not-changed", "Every declared commit path must be changed.", EXIT_REFUSED, {"paths": missing})
    staged_before = set(staged)
    try:
        add_args = ["add"]
        if force_add:
            add_args.append("-f")
        add_args.extend(["-A", "--", *declared])
        _git(project, *add_args)
        observed = index_paths(project)
        if observed != declared:
            raise IsotopeError("index-mismatch", "The staged path set does not match the semantic commit input.", EXIT_CONFLICT, {"expected": declared, "actual": observed})
        _git(project, "commit", "--quiet", "-m", reason)
    except Exception:
        newly_staged = sorted(set(index_paths(project)) - staged_before)
        try:
            unstage(project, newly_staged)
        except Exception:
            pass
        raise
    commit = head_commit(project)
    return {"status": "committed", "commit": commit, "files": declared, "reason": reason}


def semantic_status(project: Project, operating: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "branch": current_branch(project),
        "head": head_commit(project),
        "base_commit": None if operating is None else operating["base_commit"],
        "operation_branch": None if operating is None else operating["branch"],
        "operation_state": None if operating is None else operating["state"],
        "staged": index_paths(project),
        "changed": changed_paths(project),
    }


def cleanup_index(project: Project) -> dict[str, Any]:
    staged = index_paths(project)
    unstage(project, staged)
    return {"status": "clean", "unstaged": staged, "changed": changed_paths(project)}


def is_ancestor(project: Project, ancestor: str, descendant: str) -> bool:
    return _git(project, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def deterministic_squash_commit(project: Project, operation_head: str, base_commit: str, reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip() or "\n" in reason or "\r" in reason:
        raise IsotopeError("invalid-input", "Landing reason must be one non-empty canonical subject line.", EXIT_MALFORMED, {"path": "/reason"})
    tree = _git(project, "rev-parse", f"{operation_head}^{{tree}}").stdout.strip()
    fields = _git(project, "show", "-s", "--format=%an%n%ae%n%aI%n%cn%n%ce%n%cI", operation_head).stdout.splitlines()
    if len(fields) != 6:
        raise IsotopeError("git-failed", "The operation commit identity could not be read.", EXIT_CONFLICT)
    env = {
        "GIT_AUTHOR_NAME": fields[0], "GIT_AUTHOR_EMAIL": fields[1], "GIT_AUTHOR_DATE": fields[2],
        "GIT_COMMITTER_NAME": fields[3], "GIT_COMMITTER_EMAIL": fields[4], "GIT_COMMITTER_DATE": fields[5],
    }
    result = _git(project, "commit-tree", tree, "-p", base_commit, input_text=reason + "\n", env=env)
    return result.stdout.strip()


def advance_branch(project: Project, branch: str, new_commit: str, expected_commit: str) -> None:
    current = branch_commit(project, branch)
    if current == new_commit:
        return
    if current != expected_commit:
        raise IsotopeError("target-moved", "The landing target moved beyond the operation base.", EXIT_CONFLICT, {"branch": branch, "expected": expected_commit, "actual": current})
    result = _git(project, "update-ref", f"refs/heads/{branch}", new_commit, expected_commit, check=False)
    if result.returncode != 0:
        raise IsotopeError("target-moved", "The landing target changed during compare-and-swap.", EXIT_CONFLICT, {"branch": branch})


def delete_branch(project: Project, branch: str, expected_commit: str) -> None:
    if not branch_exists(project, branch):
        return
    result = _git(project, "update-ref", "-d", f"refs/heads/{branch}", expected_commit, check=False)
    if result.returncode != 0:
        raise IsotopeError("branch-moved", "The operation branch changed before cleanup.", EXIT_CONFLICT, {"branch": branch})


def _worktree_digest(project: Project, path: str) -> str | None:
    target = project.root / path
    if target.is_symlink():
        data = os.fsencode(os.readlink(target))
        return "sha256:" + hashlib.sha256(data).hexdigest()
    if not target.is_file():
        return None
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def _index_entries(project: Project) -> list[dict[str, Any]]:
    result = _git(project, "ls-files", "--stage", "-z")
    entries: list[dict[str, Any]] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        if is_managed_path(path):
            continue
        mode, blob, stage = meta.split()
        entries.append({"mode": mode, "path": path, "blob": blob, "stage": int(stage)})
    entries.sort(key=lambda entry: (entry["path"], entry["stage"]))
    return entries


def review_snapshot(project: Project, base_commit: str) -> tuple[dict[str, Any], str]:
    """Canonical manifest of every Git-visible Review input, and its revision."""
    tracked: list[dict[str, Any]] = []
    untracked: list[dict[str, Any]] = []
    for entry in status_entries(project):
        if is_managed_path(entry["path"]):
            continue
        if entry["x"] == "?" and entry["y"] == "?":
            digest = _worktree_digest(project, entry["path"])
            if digest is not None:
                untracked.append({"path": entry["path"], "digest": digest})
            continue
        if entry["y"] == "D":
            tracked.append({"path": entry["path"], "status": "deleted", "digest": None})
        elif entry["y"] in ("M", "T", "A"):
            tracked.append(
                {
                    "path": entry["path"],
                    "status": "modified",
                    "digest": _worktree_digest(project, entry["path"]),
                }
            )
    tracked.sort(key=lambda entry: entry["path"])
    untracked.sort(key=lambda entry: entry["path"])
    manifest = {
        "schema_version": "1",
        "base_commit": base_commit,
        "head_commit": head_commit(project),
        "index": _index_entries(project),
        "tracked": tracked,
        "untracked": untracked,
    }
    validate_schema("review-snapshot", manifest)
    return manifest, revision(manifest)


def snapshot_patch(project: Project, base_commit: str) -> dict[str, Any]:
    """Patch view: tracked diff against the base plus whole untracked contents."""
    exclusions = [f":(exclude){path}" for path in MANAGED_FILES]
    exclusions.extend(f":(exclude){prefix}**" for prefix in MANAGED_PREFIXES)
    diff = _git(project, "diff", "--no-color", base_commit, "--", ".", *exclusions).stdout
    untracked = []
    for entry in status_entries(project):
        if entry["x"] == "?" and entry["y"] == "?" and not is_managed_path(entry["path"]):
            target = project.root / entry["path"]
            try:
                content = (
                    os.readlink(target)
                    if target.is_symlink()
                    else target.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError):
                content = None
            untracked.append({"path": entry["path"], "content": content})
    untracked.sort(key=lambda entry: entry["path"])
    return {"diff": diff, "untracked": untracked}
