#!/usr/bin/env python3
"""Convert canonical Bitwarden items into the in-memory Ansible contract."""

from __future__ import annotations

import argparse
import json
import sys


PREFIX = "homelab-infra/"


def item_fields(item: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in item.get("fields") or []:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in fields:
            raise ValueError(f"duplicate field {name!r} in {item.get('name')!r}")
        value = field.get("value")
        fields[name] = "" if value is None else str(value)
    return fields


def build_contract(items: list[dict]) -> tuple[dict, set[str]]:
    contract: dict[str, object] = {}
    found: set[str] = set()
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name.startswith(PREFIX):
            continue
        if name in found:
            raise ValueError(f"duplicate canonical item {name!r}")
        found.add(name)
        suffix = name[len(PREFIX) :]
        fields = item_fields(item)
        if suffix.startswith("media/") or suffix.startswith("apps/"):
            group, instance = suffix.split("/", 1)
            if not instance or "/" in instance:
                raise ValueError(f"invalid application item name {name!r}")
            contract.setdefault(group, {})[instance] = fields
        elif suffix.startswith("estates/"):
            parts = suffix.split("/")
            if len(parts) != 3 or not all(parts[1:]):
                raise ValueError(f"invalid estate item name {name!r}")
            contract.setdefault("estates", {}).setdefault(parts[1], {})[parts[2]] = fields
        elif suffix and "/" not in suffix:
            contract[suffix] = fields
        else:
            raise ValueError(f"invalid canonical item name {name!r}")
    return contract, found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require", action="append", default=[])
    args = parser.parse_args()
    try:
        source = json.load(sys.stdin)
        if not isinstance(source, list):
            raise ValueError("Bitwarden item input must be a JSON list")
        contract, found = build_contract(source)
        missing = sorted(set(args.require) - found)
        if missing:
            print("missing required vault item(s): " + ", ".join(missing), file=sys.stderr)
            return 3
        json.dump(contract, sys.stdout, separators=(",", ":"))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"invalid vault item data: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
