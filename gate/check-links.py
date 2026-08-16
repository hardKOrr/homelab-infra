#!/usr/bin/env python3
"""Reject broken repository-local Markdown links and docs/*.md references."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


MARKDOWN_LINK = re.compile(r"\]\((<[^>]+>|[^)\s]+)(?:\s+['\"].*?['\"])?\)")
DOC_PATH = re.compile(r"(?<![A-Za-z0-9_./-])(docs/[A-Za-z0-9_./-]+\.md)")


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None

    path = Path(unquote(parsed.path))
    if path.is_absolute():
        return Path(str(path).lstrip("/"))
    return source.parent / path


def main() -> int:
    findings: set[tuple[str, int, str]] = set()
    files = repository_files()
    text_files = 0
    markdown_files = 0

    for path in files:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        text_files += 1
        if path.suffix.lower() == ".md":
            markdown_files += 1
            for line_number, line in enumerate(lines, 1):
                for match in MARKDOWN_LINK.finditer(line):
                    target = local_link_target(path, match.group(1))
                    if target is not None and not target.exists():
                        findings.add((path.as_posix(), line_number, match.group(1)))

        for line_number, line in enumerate(lines, 1):
            for match in DOC_PATH.finditer(line):
                target = Path(match.group(1))
                if not target.exists():
                    findings.add((path.as_posix(), line_number, match.group(1)))

    for filename, line_number, target in sorted(findings):
        print(f"{filename}:{line_number}: broken local documentation path: {target}")

    print(
        f"check-links: {markdown_files} Markdown file(s), {text_files} text file(s), "
        f"{len(findings)} broken path(s)"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
