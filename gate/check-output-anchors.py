#!/usr/bin/env python3
"""Keep operator-facing output tied to the Markdown passage it restates.

Several things this platform prints to an operator — the bootstrap summary, a job's
runtime guidance — restate a passage that a `README.md` already owns. Nothing connected
the two, so a documentation pass could correct the contract and leave the printed text
saying the superseded thing.

A passage is declared canonical in Markdown:

    <!-- output-source:network-prerequisite sha=1a2b3c4d -->
    ...the canonical passage...
    <!-- /output-source:network-prerequisite -->

Every place that restates it carries a comment naming the same id:

    # output-source:network-prerequisite — restates README.md; update both together.

This check enforces the link, not textual equality: printed text interpolates live values
and wraps to a console, so it can never be byte-identical to the prose. It fails when

- a consumer names an id no Markdown block declares,
- a declared block has no consumer left (the output was removed or moved),
- an id is declared twice, or
- a block's content no longer hashes to its declared `sha`.

Markers inside a fenced code block are examples of the convention, not uses of it, and are
ignored on both sides.

The last one is the update chain. Editing the canonical passage changes its hash, and
every consumer has to be re-read before `--update` records the new hash.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

OPEN = re.compile(r"<!--\s*output-source:([a-z0-9-]+)\s+sha=([0-9a-f]{8}|none)\s*-->")
CLOSE = re.compile(r"<!--\s*/output-source:([a-z0-9-]+)\s*-->")
CONSUMER = re.compile(r"output-source:([a-z0-9-]+)")


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def digest(body: list[str]) -> str:
    """Hash the passage's words, so rewrapping a paragraph is not a content change."""
    normalized = " ".join(" ".join(body).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


def collect(files: list[Path]) -> tuple[dict, list, list]:
    """Return declared blocks, consumer references, and structural findings."""
    blocks: dict[str, dict] = {}
    consumers: list[tuple[str, str, int]] = []
    findings: list[str] = []

    this_file = Path(__file__).resolve()
    for path in sorted(files):
        if not path.is_file():
            continue
        # This file documents the convention in its own docstring; its example ids are
        # illustrations, not consumers.
        if path.resolve() == this_file:
            continue
        lines = read_lines(path)
        if lines is None:
            continue
        markdown = path.suffix.lower() == ".md"

        open_id: str | None = None
        open_line = 0
        declared = ""
        body: list[str] = []
        fenced = False

        for number, line in enumerate(lines, 1):
            if markdown:
                # A fenced block is an example of the convention, not a use of it.
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                    if open_id is not None:
                        body.append(line)
                    continue
                if fenced:
                    if open_id is not None:
                        body.append(line)
                    continue
                start = OPEN.search(line)
                if start:
                    if open_id is not None:
                        findings.append(
                            f"{path.as_posix()}:{number}: output-source:{start.group(1)} "
                            f"opens inside the still-open block {open_id}"
                        )
                    open_id, open_line = start.group(1), number
                    declared, body = start.group(2), []
                    continue
                end = CLOSE.search(line)
                if end:
                    if open_id is None or end.group(1) != open_id:
                        findings.append(
                            f"{path.as_posix()}:{number}: closing "
                            f"output-source:{end.group(1)} without a matching opener"
                        )
                    else:
                        if open_id in blocks:
                            first = blocks[open_id]["where"]
                            findings.append(
                                f"{path.as_posix()}:{open_line}: output-source:{open_id} "
                                f"is already declared at {first}"
                            )
                        blocks[open_id] = {
                            "where": f"{path.as_posix()}:{open_line}",
                            "path": path,
                            "line": open_line,
                            "declared": declared,
                            "actual": digest(body),
                        }
                    open_id = None
                    continue
                if open_id is not None:
                    body.append(line)
                    continue

            for match in CONSUMER.finditer(line):
                if markdown:
                    continue
                consumers.append((match.group(1), path.as_posix(), number))

        if open_id is not None:
            findings.append(
                f"{path.as_posix()}:{open_line}: output-source:{open_id} is never closed"
            )

    return blocks, consumers, findings


def main() -> int:
    update = "--update" in sys.argv[1:]
    blocks, consumers, findings = collect(repository_files())
    stale: list[str] = []

    for identifier, source, number in consumers:
        if identifier not in blocks:
            findings.append(
                f"{source}:{number}: output-source:{identifier} names no canonical "
                f"Markdown passage"
            )

    used = {identifier for identifier, _, _ in consumers}
    for identifier, block in sorted(blocks.items()):
        if identifier not in used:
            findings.append(
                f"{block['where']}: output-source:{identifier} has no consumer; remove "
                f"the markers or restore the output that restates it"
            )
        if block["declared"] != block["actual"]:
            stale.append(identifier)

    if update:
        for identifier in stale:
            block = blocks[identifier]
            # Byte-level, one line. Tracked files carry mixed line endings, and a
            # whole-file rewrite would turn a one-line hash update into a full diff.
            raw = block["path"].read_bytes().splitlines(keepends=True)
            index = block["line"] - 1
            raw[index] = OPEN.sub(
                f"<!-- output-source:{identifier} sha={block['actual']} -->",
                raw[index].decode("utf-8"),
                count=1,
            ).encode("utf-8")
            block["path"].write_bytes(b"".join(raw))
            print(f"updated output-source:{identifier} -> sha={block['actual']}")
    else:
        for identifier in stale:
            block = blocks[identifier]
            places = ", ".join(
                f"{source}:{number}"
                for consumed, source, number in consumers
                if consumed == identifier
            )
            findings.append(
                f"{block['where']}: output-source:{identifier} changed "
                f"(sha={block['declared']} -> {block['actual']}). Re-read the output at "
                f"{places or 'no consumer'}, then run "
                f"gate/check-output-anchors.py --update"
            )

    for finding in findings:
        print(finding)

    print(
        f"check-output-anchors: {len(blocks)} canonical passage(s), "
        f"{len(consumers)} consumer(s), {len(findings)} problem(s)"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
