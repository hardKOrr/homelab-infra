#!/usr/bin/env python3
"""Enforce the repository structure rules in AGENTS.md.

Checks, each against the exceptions recorded in gate/structure.yml:
  size     a file stays within its kind's line budget, or within its recorded ceiling
  readme   a directory has a README.md, or its parent's README names it
  reach    every README and doc is reachable by Markdown links from AGENTS.md
  shared   two docs do not share a run of SHINGLE words (one home per fact)
  literal  a playbook does not compare an app name to a literal
  role     a catalog app has its playbook, defaults, example and roles/<app>/ (or alias)

An exception may not grow, and an exception that no longer applies must be deleted, so
the list only shrinks. Run with --update to print the current values for structure.yml.
"""

from __future__ import annotations

import collections
import fnmatch
import posixpath
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SHINGLE = 12

# First matching pattern wins; files matching none have no budget (data, fixtures, jobs).
BUDGETS = [
    ("AGENTS.md", 80),
    ("*.md", 300),
    ("ansible/playbooks/*.yml", 300),
    ("ansible/tasks/*.yml", 300),
    ("ansible/roles/*/tasks/*.yml", 300),
    ("ansible/roles/*/templates/*", 300),
    ("ansible/templates/*", 300),
    ("*.py", 600),
    ("*.sh", 600),
]

# Directories with their own fixed layout or no reader value are not mapped.
README_SKIP = ("ansible/roles/", ".github", ".claude", "gate/fixtures/")
REACH_SKIP = {"CLAUDE.md", ".github/PULL_REQUEST_TEMPLATE.md"}
LINK = re.compile(r"\]\((<[^>]+>|[^)\s#]+)")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    return sorted(p for p in out.stdout.decode().split("\0") if p and (ROOT / p).is_file())


def lines(path: str) -> int:
    with open(ROOT / path, "rb") as handle:
        return sum(1 for _ in handle)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def budget(path: str) -> int | None:
    for pattern, limit in BUDGETS:
        if fnmatch.fnmatch(path, pattern):
            return limit
    return None


def measure_size(files):
    return {p: n for p in files if (b := budget(p)) is not None and (n := lines(p)) > b}


def measure_readme(files):
    dirs = {posixpath.dirname(p) for p in files} - {""}
    dirs |= {posixpath.dirname(d) for d in dirs} - {""}
    missing = []
    for d in sorted(dirs):
        if d.startswith(README_SKIP) or d + "/" in README_SKIP:
            continue
        if (ROOT / d / "README.md").is_file():
            continue
        # The nearest README above the directory must name it by its relative path.
        above = posixpath.dirname(d)
        while above and not (ROOT / above / "README.md").is_file():
            above = posixpath.dirname(above)
        readme = ROOT / above / "README.md"
        name = posixpath.relpath(d, above or ".")
        if readme.is_file() and re.search(rf"(?<![\w/-]){re.escape(name)}/", readme.read_text()):
            continue
        missing.append(d)
    return missing


def measure_reach(files):
    docs = {p for p in files if p.endswith(".md")} - REACH_SKIP
    seen, todo = set(), ["AGENTS.md"]
    while todo:
        path = todo.pop()
        if path in seen or path not in docs:
            continue
        seen.add(path)
        for raw in LINK.findall(text(path)):
            target = raw.strip("<>")
            if "://" in target or target.startswith("mailto:"):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if (ROOT / resolved).is_dir():
                resolved = posixpath.join(resolved, "README.md")
            todo.append(resolved)
    return sorted(docs - seen)


def measure_shared(files):
    docs = [p for p in files if p.endswith(".md") and p not in REACH_SKIP]
    owners = collections.defaultdict(set)
    for path in docs:
        words = re.findall(r"\w+", text(path).lower())
        for i in range(len(words) - SHINGLE + 1):
            owners[" ".join(words[i : i + SHINGLE])].add(path)
    pairs = collections.Counter()
    for paths in owners.values():
        ordered = sorted(paths)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                pairs[f"{a} {b}"] += 1
    return dict(pairs)


def catalog_apps():
    return list(yaml.safe_load(text("catalog/applications.yml"))["applications"])


def measure_literal(files, apps):
    names = "|".join(sorted(map(re.escape, apps), key=len, reverse=True))
    # The left side is an app-name variable (app, app_name, remove_app, _ra_app, ...),
    # optionally defaulted; provider and method comparisons do not count.
    subject = r"\b\w*app(?:_name)?\b(?:\s*\|\s*default\([^)]*\))?\s*\)?\s*"
    compare = re.compile(
        rf"""{subject}==\s*['"]({names})['"]|{subject}in\s*\[[^\]]*['"]({names})['"]"""
    )
    counts = {}
    for path in files:
        if not (path.startswith("ansible/playbooks/") and path.endswith(".yml")):
            continue
        own = posixpath.splitext(posixpath.basename(path))[0]
        hits = 0
        for line in text(path).splitlines():
            if line.lstrip().startswith("#"):
                continue
            hits += sum(1 for m in compare.finditer(line) if (m.group(1) or m.group(2)) != own)
        if hits:
            counts[path] = hits
    return counts


def main() -> int:
    config = yaml.safe_load((ROOT / "gate/structure.yml").read_text()) or {}
    files = tracked()
    apps = catalog_apps()
    current = {
        "size": measure_size(files),
        "readme": measure_readme(files),
        "reach": measure_reach(files),
        "shared": measure_shared(files),
        "literal": measure_literal(files, apps),
    }
    if "--update" in sys.argv:
        print(yaml.safe_dump({k: current[k] for k in ("size", "readme", "reach", "literal")},
                             sort_keys=True, width=100))
        recorded = config.get("shared") or {}
        print(yaml.safe_dump({"shared": {k: v for k, v in current["shared"].items()
                                         if k in recorded}}, width=200))
        return 0

    problems = []
    for kind in ("size", "shared", "literal"):
        allowed = config.get(kind) or {}
        for key, value in current[kind].items():
            if key not in allowed:
                problems.append(f"{kind}: {key} is {value}, over the rule with no exception")
            elif value > allowed[key]:
                problems.append(f"{kind}: {key} grew to {value}, ceiling {allowed[key]}")
        for key in allowed:
            if key not in current[kind]:
                problems.append(f"{kind}: {key} now meets the rule; delete its exception")
    for kind in ("readme", "reach"):
        allowed = set(config.get(kind) or [])
        for key in current[kind]:
            if key not in allowed:
                problems.append(f"{kind}: {key} breaks the rule with no exception")
        for key in sorted(allowed - set(current[kind])):
            problems.append(f"{kind}: {key} now meets the rule; delete its exception")

    aliases = config.get("role_aliases") or {}
    absent = set(config.get("missing_paths") or [])
    for app in apps:
        role = aliases.get(app, app)
        expected = [f"ansible/playbooks/apps/{app}.yml", f"ansible/vars/app-defaults/{app}.yml",
                    f"config.example/apps/{app}.example.yml"]
        if role is not None:  # a null alias: the app has no role (an ISO appliance)
            expected.append(f"ansible/roles/{role}")
        for path in expected:
            exists = (ROOT / path).exists()
            if not exists and path not in absent:
                problems.append(f"role: catalog app {app} has no {path}")
            elif exists and path in absent:
                problems.append(f"role: {path} now exists; delete it from missing_paths")
    for app in aliases:
        if app not in apps:
            problems.append(f"role: alias {app} is not a catalog app; delete it")
        elif aliases[app] == app:
            problems.append(f"role: alias {app} names its own role; delete it")

    for problem in problems:
        print(problem)
    if problems:
        print("See 'Keeping the repository small' in AGENTS.md: split the file, move per-app "
              "behavior into the role, link instead of restating, or add the README entry.")
    exceptions = sum(len(config.get(k) or {}) for k in ("size", "readme", "reach", "shared", "literal"))
    print(f"check-structure: {len(files)} file(s), {exceptions} recorded exception(s), "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
