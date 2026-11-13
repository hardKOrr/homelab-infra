#!/usr/bin/env python3
"""Require application wiring to reject duplicates and read back one live object."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
from ansible.plugins.filter.core import combine

import task_parts

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
        name: list(walk_tasks(task_parts.inline(repo / path)))
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
    # Exercise the shared payload seam: existing HTTP callers keep their fields;
    # UDP callers use ping with a hostname and verify that same field on readback.
    env = Environment(undefined=StrictUndefined)
    env.filters["combine"] = combine
    payload = task(sources["kuma"], "Wire Uptime Kuma | Select the existing monitor and build the payload")["ansible.builtin.set_fact"]["_kuma_fields"]
    expression = env.compile_expression(payload.strip()[2:-2].strip())
    common = dict(wiring_app_name="wireguard", wiring_monitor_url="192.0.2.10",
                  _kuma_interval=60, _kuma_notification_id="")
    http = expression(**common)
    ping = expression(**common, wiring_monitor_type="ping")
    assert http["type"] == "http" and http["url"] == "192.0.2.10"
    assert "hostname" not in http
    assert ping["type"] == "ping" and ping["hostname"] == "192.0.2.10"
    guards = task(sources["kuma"], "Wire Uptime Kuma | Assert exactly one monitor is active")["ansible.builtin.assert"]["that"]
    for fields in (http, ping):
        context = dict(common, _kuma_verified_matches=[fields])
        if fields["type"] == "ping":
            context["wiring_monitor_type"] = "ping"
        assert all(env.compile_expression(g)(**context) for g in guards)
        fields = dict(fields, hostname="wrong", url="wrong")
        context["_kuma_verified_matches"] = [fields]
        assert not all(env.compile_expression(g)(**context) for g in guards)
    print("wiring uniqueness checks passed")


if __name__ == "__main__":
    main()
