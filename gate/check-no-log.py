#!/usr/bin/env python3
"""Refuse `no_log` on a task whose failure output holds no secret.

`no_log: true` hides a failed task's whole result, so the run stops without saying why.
A failed assert or fail prints its condition as text and its message; debug prints what
it names; stat, file, wait_for, find and ping return paths, ports and host names. None of
these needs hiding unless its message or a loop item carries a secret (the failed result
includes the whole `item`, whatever the loop's `label`).

Such a task may keep `no_log: true` only with a trailing comment on that line naming the
secret, for example `no_log: true  # loop items carry the client password`. A templated
`no_log` value is left alone.

Usage: check-no-log.py [file ...]  checks the given task files, or every `ansible/**/*.yml`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PLAIN = {"assert", "fail", "debug", "stat", "file", "wait_for", "find", "ping"}
NESTED = ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers")
LITERAL_TRUE = re.compile(r"^\s*no_log:\s*(true|True|yes)\s*(#\s*(?P<reason>\S.*))?$")


def module(keys: dict) -> str | None:
    for key in keys:
        short = key.rsplit(".", 1)[-1]
        if key.startswith(("ansible.builtin.", "ansible.legacy.")) or "." not in key:
            if short in PLAIN:
                return short
    return None


def walk(node, lines: list[str], path: Path, problems: list[str], counts: dict) -> None:
    if not isinstance(node, yaml.SequenceNode):
        return
    for item in node.value:
        if not isinstance(item, yaml.MappingNode):
            continue
        keys = {k.value: (k, v) for k, v in item.value}
        for nested in NESTED:
            if nested in keys:
                walk(keys[nested][1], lines, path, problems, counts)
        if "no_log" not in keys:
            continue
        name = module(keys)
        if name is None:
            continue
        line_no = keys["no_log"][0].start_mark.line
        match = LITERAL_TRUE.match(lines[line_no])
        if not match:
            continue
        counts["hidden"] += 1
        if not match.group("reason"):
            rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
            problems.append(f"{rel}:{line_no + 1}: {name} hides its failure; remove no_log, "
                            "or name the secret in a comment on that line")


def check(paths: list[Path]) -> tuple[list[str], int]:
    problems: list[str] = []
    counts = {"hidden": 0}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        try:
            root = yaml.compose(text)
        except yaml.YAMLError:
            continue  # yamllint and ansible-lint report the syntax
        walk(root, text.split("\n"), path, problems, counts)
    return problems, counts["hidden"]


def main(argv: list[str]) -> int:
    paths = [Path(a).resolve() for a in argv] or sorted((REPO / "ansible").rglob("*.yml"))
    problems, hidden = check(paths)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"check-no-log: {hidden} hidden assert/fail/debug/stat/file/wait_for/find/ping "
          f"task(s), {len(problems)} without a named secret")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
