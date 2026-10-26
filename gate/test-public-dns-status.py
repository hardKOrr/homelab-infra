#!/usr/bin/env python3
"""Exercise Lab Status public DNS decisions with an in-memory fake resolver."""

import http.client
import importlib.util
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment


repo = Path(__file__).resolve().parents[1]
module_path = repo / "ansible/library/lab_public_dns_status.py"
spec = importlib.util.spec_from_file_location("lab_public_dns_status", module_path)
status_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status_module)

resolvers = [
    {"name": "resolver-one", "url": "https://resolver-one.example/dns-query"},
    {"name": "resolver-two", "url": "https://resolver-two.example/dns-query"},
]


def check(name, condition):
    if not condition:
        raise AssertionError(name)


def fake_resolver_for(expected, *, missing=(), unreachable=()):
    calls = []

    def query(resolver, qname, record_type):
        calls.append((resolver["name"], qname, record_type))
        if resolver["name"] in unreachable:
            raise status_module.ResolverUnavailable("fake resolver unavailable")
        if (resolver["name"], qname, record_type) in missing:
            return set()
        return {expected[record_type]} if record_type in expected else set()

    return query, calls


def base_args(**overrides):
    values = {
        "enabled": True,
        "domain": "estate.example.test",
        "source": "static",
        "static_address": "198.51.100.42",
        "families": ["ipv4"],
        "resolvers": resolvers,
        "wildcard_label": "check-8b91",
    }
    values.update(overrides)
    return values


query, calls = fake_resolver_for({"A": "198.51.100.42"})
matches = status_module.check_public_dns(**base_args(query_fn=query))
check("static address matches", matches == "matches")
check("both resolvers checked apex and random wildcard label", len(calls) == 4)
check(
    "wildcard lookup uses the supplied random label",
    {call[1] for call in calls} == {"estate.example.test", "check-8b91.estate.example.test"},
)
query, calls = fake_resolver_for({"A": "198.51.100.42"})
static_both_families = status_module.check_public_dns(
    **base_args(families=["ipv4", "ipv6"], query_fn=query)
)
check("a static address checks only its configured record family", static_both_families == "matches")
check("static IPv4 does not request an unconfigured AAAA value", {call[2] for call in calls} == {"A"})

query, calls = fake_resolver_for({"CNAME": "ddns.example.net"})
cname = status_module.check_public_dns(
    **base_args(source="cname", cname_target="ddns.example.net", query_fn=query)
)
check("CNAME target matches", cname == "matches")
check("CNAME uses CNAME queries", {call[2] for call in calls} == {"CNAME"})

lookup_calls = []


def fake_lookup(urls, family):
    lookup_calls.append((tuple(urls), family))
    return "198.51.100.42" if family == "ipv4" else "2001:db8::42"


query, calls = fake_resolver_for({"A": "198.51.100.42", "AAAA": "2001:db8::42"})
lookup = status_module.check_public_dns(
    **base_args(
        source="lookup",
        lookup_urls=["https://echo-one.example", "https://echo-two.example"],
        families=["ipv4", "ipv6"],
        query_fn=query,
        lookup_fn=fake_lookup,
    )
)
check("Caddy lookup values match", lookup == "matches")
check("lookup checks each enabled family", [family for _, family in lookup_calls] == ["ipv4", "ipv6"])
check("IPv6 family is queried as AAAA", {call[2] for call in calls} == {"A", "AAAA"})

query, _ = fake_resolver_for({"A": "203.0.113.9"})
stale = status_module.check_public_dns(**base_args(query_fn=query))
check("different DNS address is stale", stale == "stale")

query, _ = fake_resolver_for({}, missing=())
missing = status_module.check_public_dns(**base_args(query_fn=query))
check("no DNS answers are missing", missing == "missing")

query, _ = fake_resolver_for({"A": "198.51.100.42"}, unreachable={"resolver-two"})
unreachable = status_module.check_public_dns(**base_args(query_fn=query))
check("one failed public resolver is unreachable", unreachable == "unreachable-resolver")


def no_queries(*_args):
    raise AssertionError("disabled estates must not query a resolver or lookup endpoint")


disabled = status_module.check_public_dns(
    **base_args(enabled=False, query_fn=no_queries, lookup_fn=no_queries)
)
check("disabled estate is disabled", disabled == "disabled")
duplicate_resolver = status_module.check_public_dns(
    **base_args(resolvers=[resolvers[0], resolvers[0]], query_fn=no_queries)
)
check("the configurable resolver pair must be distinct", duplicate_resolver == "unreachable-resolver")
check(
    "address lookup refuses non-HTTPS echo endpoints",
    status_module.lookup_ip(["http://echo.example.test"], "ipv4", request_fn=no_queries) is None,
)

def lookup_fallback(endpoint, _family):
    if endpoint.endswith("one.example"):
        raise http.client.HTTPException("temporary response error")
    return "198.51.100.42"


check(
    "address lookup continues after a failed echo endpoint",
    status_module.lookup_ip(
        ["https://one.example", "https://two.example"], "ipv4", request_fn=lookup_fallback
    )
    == "198.51.100.42",
)

status_playbook = yaml.safe_load(
    (repo / "ansible/playbooks/maintenance/status.yml").read_text(encoding="utf-8")
)
report_play = next(play for play in status_playbook if play.get("name") == "Status | Report")
include_task = next(
    task for task in report_play["tasks"] if task.get("name") == "Read public DNS resolution status"
)
check(
    "status task is conditional on top-level public_dns",
    "public_dns is defined" in str(include_task.get("when", "")),
)
report_task = next(task for task in report_play["tasks"] if task.get("name") == "Render the status report")
report_template = report_task["vars"]["_st_report"]
template_env = Environment(trim_blocks=True, lstrip_blocks=True)
template_env.filters["bool"] = bool
template_env.filters["strftime"] = lambda _timestamp, _format: ""
render_values = {
    "now": lambda: datetime(2026, 1, 1),
    "_st_public_dns_section": False,
    "_st_public_dns_rows": [],
    "_st_guests": [],
    "_st_unreachable": [],
    "_st_containers": [],
    "_st_k8s_nodes": [],
    "_st_k8s_apps": [],
    "_st_k8s_cron": [],
    "_st_k8s_orphans": [],
    "_st_backups": [],
    "_st_monitors": [],
    "_st_unreachable": [],
    "hostvars": {},
    "homelabinfra_infra": {},
    "groups": {},
    "k8s_volume_read_ok": False,
}
rendered_with_public_dns_off = template_env.from_string(report_template).render(**render_values)
check(
    "Lab Status output without public_dns has no public resolution section",
    "PUBLIC RESOLUTION" not in rendered_with_public_dns_off,
)
rendered_with_public_dns_on = template_env.from_string(report_template).render(
    **(render_values | {
        "_st_public_dns_section": True,
        "_st_public_dns_rows": [{"estate": "sample", "status": "matches", "apps": ["wordpress"]}],
    })
)
check(
    "Lab Status renders the estate status and public app list",
    "sample             matches" in rendered_with_public_dns_on
    and "public apps: wordpress" in rendered_with_public_dns_on,
)

ansible_playbook = os.environ.get(
    "GATE_ANSIBLE_PLAYBOOK",
    str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
)
integration_playbook = {
    "name": "Exercise the public DNS status task without network access",
    "hosts": "localhost",
    "connection": "local",
    "gather_facts": False,
    "vars": {
        "homelabinfra_config": {
            "infrastructure": {
                "public_dns": {
                    "families": ["ipv4"],
                    "address": {"source": "static", "address": "198.51.100.42"},
                },
                "domains": {
                    "sample": {
                        "domain": "estate.example.test",
                        "default": True,
                        "public_dns": {"enabled": False},
                    }
                },
            }
        },
        "_luv_config_dir": "",
    },
    "tasks": [
        {
            "name": "Confirm the custom module loads without network requests when disabled",
            "lab_public_dns_status": {
                "enabled": False,
                "domain": "estate.example.test",
                "source": "static",
                "resolvers": [],
            },
            "register": "_status_module_result",
            "no_log": True,
        },
        {
            "name": "Add a deployed public app fixture",
            "ansible.builtin.add_host": {
                "name": "public-app-fixture",
                "groups": ["lab_managed"],
                "proxmox_tags_parsed": ["_+lab", "_hi-events"],
            },
        },
        {
            "name": "Run the public DNS status task file with one disabled estate",
            "ansible.builtin.include_tasks": str(
                repo / "ansible/tasks/maintenance/status-public-dns.yml"
            ),
        },
        {
            "name": "Confirm the safe status row is assembled",
            "ansible.builtin.assert": {
                "that": [
                    "_status_module_result.status == 'disabled'",
                    "_st_public_dns_rows | length == 1",
                    "_st_public_dns_rows[0].status == 'disabled'",
                    "_st_public_dns_rows[0].estate == 'sample'",
                    "_st_public_dns_apps | length == 1",
                    "_st_public_dns_apps[0].name == 'hi-events'",
                    "_st_public_dns_apps[0].estate == 'sample'",
                ]
            },
        },
    ],
}
with tempfile.TemporaryDirectory(prefix="public-dns-status-config-") as config_dir:
    integration_playbook["vars"]["_luv_config_dir"] = config_dir
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yml",
        encoding="utf-8",
        dir=repo / "ansible/playbooks/maintenance",
    ) as playbook_file:
        yaml.safe_dump([integration_playbook], playbook_file, sort_keys=False)
        playbook_file.flush()
        environment = os.environ.copy()
        environment["ANSIBLE_CONFIG"] = str(repo / "ansible/ansible.cfg")
        integration = subprocess.run(
            [ansible_playbook, "-i", "localhost,", playbook_file.name],
            cwd=repo / "ansible",
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
if integration.returncode != 0:
    raise AssertionError(
        "Ansible loads the module and assembles disabled estate rows\n"
        f"{integration.stdout}\n{integration.stderr}"
    )

print("Public DNS status fake-resolver states passed")
