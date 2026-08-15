#!/usr/bin/env python3
"""Remove every trace of one instance from the service registry.

Reads `{"registry": {...}, "instance": "<name>"}` on stdin and writes the pruned
registry as JSON on stdout, so the caller can compare it with what it had and write
the file only when something actually changed.

WHY. `config/.generated/facts.yml` is how each service finds the ones before it, and
until 2026-08-15 nothing ever removed from it. `Remove App` unwires the reverse proxy,
SSO, uptime monitoring and DNS, stops the app and can delete its data — and leaves its
registry entry behind, pointing at an endpoint that is gone.

That is not cosmetic. `tasks/app-wiring/resolve-media-registry.yml` treats an entry with
an `app` kind and a `host` as USABLE, so the next `Wire Media Stack` run registers the
removed app as a download client in every *arr and then fails when it does not answer.
Measured on the live lab: `media.sabnzbd-foxglove` survived the removal of the app
(execution 153) and its container no longer exists.

Two shapes are pruned, at the top level and inside every `estates.<name>` scope:

  1. `media.<instance>` — the per-instance media registry entry.
  2. any role entry (`sso`, `monitoring`, `notifications`, …) whose `instance` field
     names this instance — the removed app WAS that service, so the registry must stop
     claiming the lab has one.

Nothing else is touched: `domain`, unrelated roles, other estates and other instances
come back byte-identical. Empty containers left behind by a prune are dropped too — an
`estates.foxglove` holding an empty `media` map is a claim about the estate that is no
longer true.
"""

from __future__ import annotations

import json
import sys


def prune_scope(scope: dict, instance: str) -> dict:
    """Prune one registry scope — the top level, or one estates.<name> block."""
    result: dict[str, object] = {}
    for key, value in scope.items():
        if key == "estates":
            continue  # walked separately by prune()
        if not isinstance(value, dict):
            result[key] = value
            continue
        if key == "media":
            remaining = {k: v for k, v in value.items() if k != instance}
            if remaining:
                result[key] = remaining
            continue
        # A role entry naming this instance describes a service that no longer exists.
        if value.get("instance") == instance:
            continue
        result[key] = value
    return result


def prune(registry: dict, instance: str) -> dict:
    result = prune_scope(registry, instance)
    estates = registry.get("estates")
    if isinstance(estates, dict):
        pruned_estates = {}
        for name, scope in estates.items():
            if not isinstance(scope, dict):
                pruned_estates[name] = scope
                continue
            pruned = prune_scope(scope, instance)
            if pruned:
                pruned_estates[name] = pruned
        if pruned_estates:
            result["estates"] = pruned_estates
    return result


def main() -> int:
    try:
        source = json.load(sys.stdin)
        if not isinstance(source, dict):
            raise ValueError("input must be a JSON object")
        registry = source.get("registry")
        instance = source.get("instance")
        if registry is None:
            registry = {}
        if not isinstance(registry, dict):
            raise ValueError("registry must be a mapping")
        if not isinstance(instance, str) or not instance:
            raise ValueError("instance must be a non-empty string")
        json.dump(prune(registry, instance), sys.stdout, separators=(",", ":"))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"invalid registry input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
