#!/usr/bin/env python3
"""Require application wiring to reject duplicates and read back one live object."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]


def walk_tasks(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("name"):
            yield item
        for key in ("block", "rescue", "always"):
            nested = item.get(key, [])
            if isinstance(nested, list):
                yield from walk_tasks(nested)


def task(source, name):
    matches = [item for item in source if item.get("name") == name]
    assert len(matches) == 1, f"expected one task named {name!r}, found {len(matches)}"
    return matches[0]


def conditions(item):
    value = item.get("ansible.builtin.assert", {}).get("that", [])
    return " ".join(value if isinstance(value, list) else [str(value)])


def require_guard(source, name, expression):
    item = task(source, name)
    assert item.get("no_log") is True, f"{name} may expose live provider data"
    assert expression in conditions(item), f"{name} must enforce {expression!r}"


def main():
    sources = {
        name: list(walk_tasks(yaml.safe_load((repo / path).read_text())))
        for name, path in {
            "caddy": "ansible/tasks/wiring/caddy.yml",
            "dns": "ansible/tasks/wiring/opnsense.yml",
            "authentik": "ansible/tasks/wiring/authentik.yml",
            "kuma": "ansible/tasks/wiring/uptime-kuma.yml",
        }.items()
    }

    require_guard(
        sources["caddy"], "Wire Caddy | Reject duplicate routes for this instance",
        "_caddy_route_matches | length <= 1",
    )
    assert "Wire Caddy | Assert exactly one route is active" in {
        item.get("name") for item in sources["caddy"]
    }

    require_guard(
        sources["dns"], "Wire OPNsense | Reject duplicate exact host overrides",
        "_opn_existing_matches | length <= 1",
    )
    require_guard(
        sources["dns"], "Wire OPNsense | Assert exactly one host override is active",
        "_opn_verified_matches | length == 1",
    )

    for name, expr in [
        ("Wire Authentik | Reject duplicate proxy providers", "_ak_provider_matches | length <= 1"),
        ("Wire Authentik | Reject duplicate OAuth2 providers", "_ak_oauth_matches | length <= 1"),
        ("Wire Authentik | Reject duplicate application slugs", "_ak_app_matches | length <= 1"),
    ]:
        require_guard(sources["authentik"], name, expr)
    assert "Wire Authentik | Assert exactly one application is active" in {
        item.get("name") for item in sources["authentik"]
    }

    require_guard(
        sources["kuma"], "Wire Uptime Kuma | Reject duplicate monitors for this instance",
        "_kuma_existing_matches | length <= 1",
    )
    require_guard(
        sources["kuma"], "Wire Uptime Kuma | Assert exactly one monitor is active",
        "_kuma_verified_matches | length == 1",
    )
    print("wiring uniqueness checks passed")


if __name__ == "__main__":
    main()
