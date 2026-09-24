#!/usr/bin/env python3
"""Reject the operator's real lab identifiers in public text.

This repository and its GitHub threads are public. docs/lab-placeholders.md defines the
placeholder written in place of every lab-identifying value; the real values live in a
private file outside the repository (default ~/.config/ai/homelab-infra/lab-values.yml,
overridable with HOMELAB_LAB_VALUES). This check reads that file and fails when any of its
values appears in:

  - tracked and untracked-but-not-ignored files (the default),
  - text on stdin (--stdin), for an issue, PR, or comment body before it is posted,
  - commit messages in a revision range (--log <range>).

A finding names the location and the placeholder key, never the value, so the check's own
output is safe to paste anywhere. Without the values file (CI, a contributor's machine)
there is nothing to compare against: the check says so and passes.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

DEFAULT_VALUES = Path.home() / ".config" / "ai" / "homelab-infra" / "lab-values.yml"


def flatten(node: object, prefix: str = "") -> list[tuple[str, str]]:
    """Every non-empty scalar in the values file, labelled by its key path."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, child in node.items():
            out.extend(flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            out.extend(flatten(child, f"{prefix}[{index}]"))
    elif node not in (None, ""):
        out.append((prefix, str(node)))
    return out


def pattern_for(value: str) -> re.Pattern[str]:
    """Match the value as a whole token, so node-1 never matches inside node-10.

    An address-like value must also not continue into a longer address: 10.0.0.1 is not a
    hit inside 10.0.0.14. A name may follow a dot (auth.<lab-domain>).
    """
    body = re.escape(value)
    if value[:1].isdigit():
        return re.compile(rf"(?<![\d.]){body}(?![\d]|\.\d)")
    return re.compile(rf"(?<![A-Za-z0-9_-]){body}(?![A-Za-z0-9_])", re.IGNORECASE)


def load_rules(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    data = yaml.safe_load(path.read_text()) or {}
    seen: set[str] = set()
    rules = []
    for label, value in flatten(data):
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        rules.append((label, pattern_for(value)))
    return rules


def scan(text: str, where: str, rules) -> list[str]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for label, rx in rules:
            if rx.search(line):
                findings.append(f"{where}:{number}: <{label.rsplit('.', 1)[-1]}> ({label})")
    return findings


def repo_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdin", action="store_true", help="scan text on stdin")
    mode.add_argument("--log", metavar="RANGE", help="scan commit messages in RANGE")
    parser.add_argument("files", nargs="*", help="scan only these files")
    args = parser.parse_args()

    values = Path(os.environ.get("HOMELAB_LAB_VALUES", DEFAULT_VALUES)).expanduser()
    if not values.is_file():
        print(f"check-lab-leaks: skipped — no lab values file at {values}")
        return 0
    rules = load_rules(values)

    findings: list[str] = []
    if args.stdin:
        findings = scan(sys.stdin.read(), "<stdin>", rules)
    elif args.log:
        log = subprocess.run(
            ["git", "log", "--format=%H%n%B%x00", args.log],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for entry in filter(None, (e.strip() for e in log.split("\0"))):
            sha, _, body = entry.partition("\n")
            findings.extend(scan(body, f"commit {sha[:12]}", rules))
    else:
        for name in args.files or repo_files():
            path = Path(name)
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            findings.extend(scan(text, name, rules))

    if findings:
        print("check-lab-leaks: real lab identifiers found; replace each with its "
              "placeholder from docs/lab-placeholders.md:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"check-lab-leaks: OK ({len(rules)} private values checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
