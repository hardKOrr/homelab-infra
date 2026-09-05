#!/usr/bin/env python3
"""Reject secret-shaped fixture/artifact fields and a tracked config/ path.

Issue #34's first acceptance criterion: a fixture or artifact candidate carrying a
prohibited secret-shaped key/value, or a tracked path under config/, must fail the gate
before it ever reaches a hosted PR run. New test infrastructure under gate/fixtures/ and
ansible/molecule/ is exactly the second risk surface #34 describes — a fixture file is
free-form YAML a contributor can paste real-looking values into, unlike the reviewed
task/role code the existing lint already covers.

Reuses the exact secret-shape regex ansible/scripts/secret-shape.py enforces on generated
facts, so a key name that would be rejected there is rejected here too — one contract, not
two definitions that can drift apart.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import re

import yaml

# The script's own location, used only to find secret-shape.py — that contract must be
# loaded from the real repository regardless of where the check is run from. Everything
# else (tracked-file scanning) runs against the process's current directory, so a focused
# test can point it at a throwaway sandbox repo.
SCRIPT_REPO = Path(__file__).resolve().parent.parent

# Fixture/artifact directories: tracked test-only input a contributor edits directly,
# as opposed to generated output or reviewed production task/role/var files those other
# gates already cover.
FIXTURE_DIRS = ("gate/fixtures/", "ansible/molecule/")

# A secret-shaped key is a legitimate part of the documented schema (e.g.
# proxmox.api_token_secret, per ansible/vars/CONTRACT.md) and fixtures must exercise it.
# What must never appear is a value that could pass for a real credential rather than an
# obvious, reviewable placeholder — the same convention config.example/ and the existing
# fixtures under gate/fixtures/config*/ already use.
PLACEHOLDER = re.compile(
    r"fixture|not.a.real|not.real|fake|dummy|placeholder|changeme|redacted|^example|^test",
    re.I,
)


def _load_secret_shape():
    spec = importlib.util.spec_from_file_location(
        "secret_shape", SCRIPT_REPO / "ansible" / "scripts" / "secret-shape.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def find_secret_paths(secret_shape, value: object, prefix: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            if secret_shape.secret_key(key):
                if isinstance(child, (dict, list)):
                    found.append(current)
                elif child not in (None, "") and not PLACEHOLDER.search(str(child)):
                    found.append(current)
            else:
                found.extend(find_secret_paths(secret_shape, child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_secret_paths(secret_shape, child, f"{prefix}[{index}]"))
    return found


def main() -> int:
    secret_shape = _load_secret_shape()
    findings: list[str] = []

    files = tracked_files()

    for path in files:
        if path == "config" or path.startswith("config/"):
            findings.append(f"{path}: tracked path under config/ — user config must never be committed")

    for path in files:
        if not any(path.startswith(d) for d in FIXTURE_DIRS):
            continue
        if not path.endswith((".yml", ".yaml")):
            continue
        full = Path(path)
        try:
            data = yaml.safe_load(full.read_text()) or {}
        except yaml.YAMLError as exc:
            findings.append(f"{path}: could not parse as YAML ({exc})")
            continue
        for secret_path in find_secret_paths(secret_shape, data):
            findings.append(f"{path}: secret-shaped key at {secret_path}")

    if findings:
        print("check-fixture-secrets: prohibited secret-shaped value(s) or tracked config/ path found:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    print(f"check-fixture-secrets: {len(files)} tracked file(s) scanned, 0 problem(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
