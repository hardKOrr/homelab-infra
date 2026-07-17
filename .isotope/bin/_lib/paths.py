"""Project discovery and consumer-repository path vocabulary."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from .errors import EXIT_NOT_FOUND, IsotopeError


ISOTOPE_DIR = ".isotope"
MANIFEST_FILE = "isotope.json"
OPERATING_FILE = "operating.json"
JOURNAL_FILE = "journal.json"
EVIDENCE_DIR = "quanta"
CULTURES_DIR = "cultures"
CULTURE_STAGES = ("matter", "flux", "stable")
INVOCATIONS_DIR = "invocations"
TOOLS_DIR = "valence"

# Lifecycle state is excluded from Review snapshots and clean-baseline checks.
# Setup-owned launchers and native assets are intentionally absent: they remain
# explicit, Git-visible Review sources.
MANAGED_PREFIXES = (
    f"{ISOTOPE_DIR}/{CULTURES_DIR}/",
    f"{ISOTOPE_DIR}/{INVOCATIONS_DIR}/",
    f"{ISOTOPE_DIR}/{EVIDENCE_DIR}/",
)
MANAGED_FILES = (
    f"{ISOTOPE_DIR}/{OPERATING_FILE}",
    f"{ISOTOPE_DIR}/{JOURNAL_FILE}",
)

SETUP_FILES = (
    f"{ISOTOPE_DIR}/{MANIFEST_FILE}",
    f"{ISOTOPE_DIR}/registry.json",
    f"{ISOTOPE_DIR}/synthesis.json",
)
SETUP_PREFIXES = (
    f"{ISOTOPE_DIR}/bin/",
    ".claude/agents/isotope-",
    ".claude/skills/isotope-",
    ".codex/agents/isotope-",
    ".agents/skills/isotope-",
)


def is_managed_path(relative: str) -> bool:
    """True when a repo-relative POSIX path is Isotope-managed lifecycle state."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return False
    path = PurePosixPath(relative)
    parts = path.parts
    if path.is_absolute() or any(part in ("", ".", "..") for part in parts):
        return False
    if path.as_posix() != relative:
        return False
    return relative in MANAGED_FILES or any(
        relative.startswith(prefix) for prefix in MANAGED_PREFIXES
    )


def is_journal_destination(relative: str, journal_type: str) -> bool:
    """Return whether one transaction type owns a normalized destination."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return False
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return False
    if path.as_posix() != relative:
        return False
    if journal_type == "any":
        return is_managed_path(relative) or relative in SETUP_FILES or any(relative.startswith(item) for item in SETUP_PREFIXES)
    if journal_type == "setup":
        return relative in SETUP_FILES or any(relative.startswith(item) for item in SETUP_PREFIXES)
    if journal_type == "operating":
        return relative == f"{ISOTOPE_DIR}/{OPERATING_FILE}"
    if journal_type == "invocation":
        return relative.startswith(f"{ISOTOPE_DIR}/{INVOCATIONS_DIR}/")
    if journal_type == "quanta":
        return relative.startswith(f"{ISOTOPE_DIR}/{EVIDENCE_DIR}/")
    if journal_type == "specimen":
        return relative.startswith(f"{ISOTOPE_DIR}/{CULTURES_DIR}/") or relative == f"{ISOTOPE_DIR}/{OPERATING_FILE}"
    if journal_type == "brokered-result":
        return (
            relative.startswith(f"{ISOTOPE_DIR}/{CULTURES_DIR}/")
            or relative.startswith(f"{ISOTOPE_DIR}/{INVOCATIONS_DIR}/")
            or relative == f"{ISOTOPE_DIR}/{OPERATING_FILE}"
        )
    return False


@dataclass(frozen=True)
class Project:
    root: Path
    git_common_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "git_common_dir": str(self.git_common_dir),
        }


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise IsotopeError(
            "project-not-found",
            "Git is unavailable; Isotope requires a Git worktree.",
            EXIT_NOT_FOUND,
            {"path": str(cwd), "reason": str(exc)},
        ) from exc
    if result.returncode != 0:
        raise IsotopeError(
            "project-not-found",
            "No Git project could be resolved from the requested path.",
            EXIT_NOT_FOUND,
            {"path": str(cwd)},
        )
    return result.stdout.strip()


def resolve_project(override: str | None, cwd: Path | None = None) -> Project:
    requested = Path(override).expanduser() if override else (cwd or Path.cwd())
    try:
        requested = requested.resolve(strict=True)
    except OSError as exc:
        raise IsotopeError(
            "project-not-found",
            "The requested project path does not exist or cannot be resolved.",
            EXIT_NOT_FOUND,
            {"path": str(requested)},
        ) from exc
    if not requested.is_dir():
        raise IsotopeError(
            "project-not-found",
            "The requested project path is not a directory.",
            EXIT_NOT_FOUND,
            {"path": str(requested)},
        )

    root = Path(_git(requested, "rev-parse", "--show-toplevel")).resolve()
    common_raw = Path(_git(requested, "rev-parse", "--git-common-dir"))
    if not common_raw.is_absolute():
        common_raw = requested / common_raw
    return Project(root=root, git_common_dir=common_raw.resolve())
