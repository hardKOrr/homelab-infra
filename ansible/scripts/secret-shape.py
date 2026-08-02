#!/usr/bin/env python3
"""Reject or remove credential-shaped fields from generated topology facts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


SECRET = re.compile(
    r"(^|_)(password|passphrase|secret|private_key|api_key|access_key|auth_key|encryption_key|credential|arl|webhook|cookie)($|_)",
    re.I,
)
TOKEN = re.compile(r"(^|_)token($|_)", re.I)
SAFE = {"api_token_id", "token_id", "tokenid", "client_id", "notification_id"}


def secret_key(key: object) -> bool:
    text = str(key).lower()
    return text not in SAFE and bool(SECRET.search(text) or TOKEN.search(text))


def paths(value: object, prefix: str = "") -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            if secret_key(key):
                result.append(current)
            else:
                result.extend(paths(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(paths(child, f"{prefix}[{index}]"))
    return result


def sanitized(value: object) -> object:
    if isinstance(value, dict):
        return {key: sanitized(child) for key, child in value.items() if not secret_key(key)}
    if isinstance(value, list):
        return [sanitized(child) for child in value]
    return value


def extracted(value: object, result: dict[str, str] | None = None) -> dict[str, str]:
    """Return secret leaves by field name for migration into an app item."""
    if result is None:
        result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if secret_key(key):
                if isinstance(child, (dict, list)):
                    raise ValueError(f"secret-shaped field {key!r} must be a scalar")
                if key in result and result[key] != str(child):
                    raise ValueError(f"duplicate secret field name {key!r}")
                if child not in (None, ""):
                    result[str(key)] = str(child)
            else:
                extracted(child, result)
    elif isinstance(value, list):
        for child in value:
            extracted(child, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--sanitize", metavar="FILE")
    action.add_argument("--extract", metavar="FILE")
    args = parser.parse_args()
    if args.sanitize:
        target = Path(args.sanitize)
        data = yaml.safe_load(target.read_text()) or {}
        target.write_text(yaml.safe_dump(sanitized(data), sort_keys=False))
        return 0
    if args.extract:
        target = Path(args.extract)
        try:
            data = yaml.safe_load(target.read_text()) or {}
            json.dump(extracted(data), sys.stdout, separators=(",", ":"))
            return 0
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"cannot extract secret fields from {target}: {exc}", file=sys.stderr)
            return 2
    data = json.load(sys.stdin)
    found = paths(data)
    if found:
        print("credential-shaped generated fact field(s): " + ", ".join(found), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
