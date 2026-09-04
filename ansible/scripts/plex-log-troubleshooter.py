#!/usr/bin/env python3
"""Read Plex logs and emit evidence-ranked, client-attributed findings."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def read_logs(bundle: Path) -> list[tuple[str, str]]:
    if not bundle.is_dir():
        raise ValueError(f"log path is not a directory: {bundle}")
    lines: list[tuple[str, str]] = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        if "plex" not in path.name.lower() and "transcoder" not in path.name.lower():
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                lines.extend((str(path.relative_to(bundle)), line.rstrip("\n")) for line in handle)
        except OSError as error:
            raise ValueError(f"cannot read {path}: {error}") from error
    return lines


def header(line: str, name: str) -> str:
    match = re.search(rf"(?:^|[\r\n; ]){re.escape(name)}[=:] ?([^\r\n;]+)", line, re.I)
    return match.group(1).strip() if match else ""


def client_of(line: str) -> dict[str, str]:
    return {
        "model": header(line, "X-Plex-Model") or "unknown model",
        "product": header(line, "X-Plex-Product") or header(line, "X-Plex-Device") or "unknown client",
        "version": header(line, "X-Plex-Version") or "unknown version",
        "platform": header(line, "X-Plex-Platform") or "unknown platform",
    }


def finding(signature: dict, *, count: int, evidence: dict) -> dict:
    return {
        "id": signature["id"], "title": signature["title"], "subject": signature["subject"],
        "severity": signature["severity"], "rank": signature["rank"], "count": count,
        "evidence": evidence,
    }


def oversized_hls(signature: dict, lines: list[tuple[str, str]]) -> list[dict]:
    pattern = re.compile(signature["pattern"], re.I)
    limit = int(signature["max_referer_bytes"])
    clients: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for source, line in lines:
        if not pattern.search(line):
            continue
        referer = header(line, "Referer")
        # `ET` is Plex logging a buffer after consuming the G; only an oversize header qualifies.
        if len(referer.encode("utf-8")) <= limit:
            continue
        client = client_of(line)
        clients[tuple(client.values())].append({"source": source, "referer_bytes": len(referer.encode("utf-8"))})
    results = []
    for key, events in clients.items():
        client = dict(zip(("model", "product", "version", "platform"), key))
        results.append(finding(signature, count=len(events), evidence={
            "client": client,
            "request_disposition": "rejected by Plex HTTP parser before playback",
            "referer_bytes": sorted({event["referer_bytes"] for event in events}),
            "sources": sorted({event["source"] for event in events}),
            "normalization": "ET request prefix is a Plex logging artifact; oversize Referer is the evidence.",
        }))
    return results


def credits_webvtt(signature: dict, lines: list[tuple[str, str]]) -> list[dict]:
    failed = re.compile(signature["pattern"], re.I)
    support = re.compile(signature["supporting_pattern"], re.I)
    release = re.compile(signature["release_group_pattern"], re.I)
    if not any(support.search(line) for _, line in lines):
        return []
    groups: Counter[str] = Counter()
    sources: set[str] = set()
    for source, line in lines:
        if not failed.search(line):
            continue
        match = release.search(line)
        groups[match.group(1) if match else "unattributed"] += 1
        sources.add(source)
    if not groups:
        return []
    return [finding(signature, count=sum(groups.values()), evidence={
        "cause": "Plex credits detection cannot decode a muxed WebVTT subtitle track.",
        "release_groups": dict(groups.most_common()), "sources": sorted(sources),
    })]


def rejected_fs(signature: dict, lines: list[tuple[str, str]]) -> list[dict]:
    pattern = re.compile(signature["pattern"], re.I)
    matched = [(source, line) for source, line in lines if pattern.search(line)]
    if not matched:
        return []
    return [finding(signature, count=len(matched), evidence={
        "disposition": "rejected by Plex HTTP parser; no handler execution is evidenced",
        "sources": sorted({source for source, _ in matched}),
        "guidance": "Informational background probe traffic. Do not infer a vhost misconfiguration from names or forged user agents.",
    })]


MATCHERS = {
    "oversized_hls_request": oversized_hls,
    "credits_webvtt_release_group": credits_webvtt,
    "rejected_fs_probe": rejected_fs,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--signatures-json", required=True)
    args = parser.parse_args()
    signatures = json.loads(args.signatures_json)
    if not isinstance(signatures, list):
        raise ValueError("signatures must be a JSON list")
    lines = read_logs(args.bundle)
    findings: list[dict] = []
    for signature in signatures:
        matcher = MATCHERS.get(signature.get("matcher"))
        if matcher is None:
            raise ValueError(f"unknown Plex signature matcher: {signature.get('matcher')!r}")
        findings.extend(matcher(signature, lines))
    findings.sort(key=lambda item: (item["rank"], item["id"], str(item["evidence"])))
    print(json.dumps({
        "files_read": len({source for source, _ in lines}), "lines_read": len(lines),
        "findings": findings,
        "summary": "No ranked Plex findings." if not findings else f"{len(findings)} ranked Plex finding(s).",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, json.JSONDecodeError) as error:
        print(f"plex-log-troubleshooter: {error}", file=sys.stderr)
        raise SystemExit(2)
