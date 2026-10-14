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


def require_actionable_guard(source, name, expression, guidance):
    item = task(source, name)
    assert expression in conditions(item), f"{name} must enforce {expression!r}"
    assert item.get("no_log") is not True, f"{name} hides its cleanup guidance"
    message = " ".join(
        item.get("ansible.builtin.assert", {}).get("fail_msg", "").split()
    )
    for phrase in guidance:
        assert phrase in message, f"{name} must tell operators {phrase!r}"


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

    require_actionable_guard(
        sources["caddy"], "Wire Caddy | Reject duplicate routes for this instance",
        "_caddy_route_matches | length <= 1",
        ["id '{{ _caddy_route_id }}'", "DELETE /id/{{ _caddy_route_id }}", "rerun"],
    )
    require_actionable_guard(
        sources["caddy"], "Wire Caddy | Assert exactly one route is active",
        "(_caddy_route_list_after.json | default([])",
        ["id '{{ _caddy_route_id }}'", "Caddy admin API", "DELETE /id/{{ _caddy_route_id }}", "rerun"],
    )

    require_actionable_guard(
        sources["dns"], "Wire OPNsense | Reject duplicate exact host overrides",
        "_opn_existing_matches | length <= 1",
        ["homelab-infra: {{ wiring_app_name }}", "Services > Unbound DNS > Overrides", "rerun"],
    )
    require_actionable_guard(
        sources["dns"], "Wire OPNsense | Assert exactly one host override is active",
        "_opn_verified_matches | length == 1",
        ["homelab-infra: {{ wiring_app_name }}", "Services > Unbound DNS > Overrides", "rerun"],
    )

    for name, expr, guidance in [
        (
            "Wire Authentik | Reject duplicate proxy providers",
            "_ak_provider_matches | length <= 1",
            ["proxy providers named '{{ wiring_app_name }}'", "Proxy Providers", "rerun"],
        ),
        (
            "Wire Authentik | Assert exactly one proxy provider is active",
            "(_ak_provider_verify.json.results | default([])",
            ["proxy provider named", "Proxy Providers", "remove any stale duplicate", "rerun"],
        ),
        (
            "Wire Authentik | Reject duplicate OAuth2 providers",
            "_ak_oauth_matches | length <= 1",
            ["OAuth2 providers named '{{ wiring_app_name }}'", "OAuth2/OpenID Providers", "rerun"],
        ),
        (
            "Wire Authentik | Assert exactly one OAuth2 provider is active",
            "(_ak_oauth_verify.json.results | default([])",
            ["OAuth2 provider named", "OAuth2/OpenID Providers", "remove any stale duplicate", "rerun"],
        ),
        (
            "Wire Authentik | Reject duplicate application slugs",
            "_ak_app_matches | length <= 1",
            ["slug '{{ wiring_app_name }}'", "Applications > Applications", "rerun"],
        ),
        (
            "Wire Authentik | Assert exactly one application is active",
            "(_ak_app_verify.json.results | default([])",
            ["application with slug", "Applications > Applications", "remove any stale duplicate", "rerun"],
        ),
    ]:
        require_actionable_guard(sources["authentik"], name, expr, guidance)

    require_actionable_guard(
        sources["kuma"], "Wire Uptime Kuma | Reject duplicate monitors for this instance",
        "_kuma_existing_matches | length <= 1",
        ["monitors named '{{ wiring_app_name }}'", "Uptime Kuma dashboard", "rerun"],
    )
    require_actionable_guard(
        sources["kuma"], "Wire Uptime Kuma | Assert exactly one monitor is active",
        "_kuma_verified_matches | length == 1",
        ["monitor named '{{ wiring_app_name }}'", "Uptime Kuma dashboard", "rerun"],
    )
    print("wiring uniqueness checks passed")


if __name__ == "__main__":
    main()
