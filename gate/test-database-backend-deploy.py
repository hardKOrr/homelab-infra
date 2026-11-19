#!/usr/bin/env python3
"""Check the database backends' Deploy path through playbooks/apps/deploy.yml.

Checks: each backend playbook is a deploy.yml shim; its role has the deploy-prepare and
deploy-record hooks on the shared database bodies; its Kubernetes entry opens the port for
registered consumers before anything else; it has no route, so deploy.yml wires no proxy,
SSO, monitor or DNS name and records no URL; and the Vaultwarden item reaches only the
Kubernetes method where the engine keeps its server credential there; a MariaDB or MySQL
Docker backend refuses a stack host holding the other's conflicting client. The registry entry
the hook writes is rendered in gate/test-database-facts.py, placement in
gate/test-database-backend-placement.py.
"""

from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


REPO = Path(__file__).resolve().parents[1]
ENGINES = ("postgresql", "mariadb", "mysql", "redis")
# Engines whose Vaultwarden item holds a server credential only on Kubernetes; Redis keeps
# its ACL passwords in the item on every method.
KUBERNETES_VAULT = {"postgresql", "mariadb", "mysql"}


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def render(expression, **variables):
    body = expression.strip()
    assert body.startswith("{{") and body.endswith("}}"), expression
    environment = Environment(undefined=StrictUndefined)
    environment.filters["bool"] = lambda value: value in (True, "True", "true", "yes", 1)
    return environment.compile_expression(body[2:-2])(**variables)


def check_engine(engine):
    defaults = read_yaml(f"ansible/vars/app-defaults/{engine}.yml")[f"{engine}_defaults"]
    assert defaults["hosting"]["method"] == "native"
    assert defaults["proxmox"]["guest_type"] in ("lxc", "vm")
    assert defaults["routing"]["proxy"] == "none" and defaults["routing"]["identity"] == "none"
    methods = (defaults.get("vault") or {}).get("methods")
    assert methods == (["kubernetes"] if engine in KUBERNETES_VAULT else None), (engine, methods)

    [shim] = read_yaml(f"ansible/playbooks/apps/{engine}.yml")
    assert shim["ansible.builtin.import_playbook"] == "deploy.yml" and shim["vars"]["app"] == engine

    [prepare] = read_yaml(f"ansible/roles/{engine}/tasks/deploy-prepare.yml")
    assert prepare["ansible.builtin.include_tasks"] == "../../../tasks/database/deploy-prepare.yml"
    [record] = read_yaml(f"ansible/roles/{engine}/tasks/deploy-record.yml")
    assert record["ansible.builtin.include_tasks"] == "../../../tasks/database/deploy-record.yml"

    first = read_yaml(f"ansible/roles/{engine}/tasks/kubernetes.yml")[0]
    assert first["ansible.builtin.include_tasks"].endswith("tasks/database/open-consumer-access.yml")
    assert first["vars"]["database_consumer_provider"] == engine


def check_deploy_unrouted():
    plays = read_yaml("ansible/playbooks/apps/deploy.yml")
    derive = next(t for t in plays[0]["tasks"] if "_deploy_routed" in t.get("ansible.builtin.set_fact", {}))
    expression = derive["ansible.builtin.set_fact"]["_deploy_routed"]
    assert render(expression, app_config={"routing": {"proxy": "none"}}) is False
    assert render(expression, app_config={"routing": {}}) is True
    # An unrouted backend deploys before any domain fact exists: its hostname is the bare
    # subdomain. A routed app still requires the domain.
    domain = derive["vars"]["_deploy_domain"]
    fqdn = derive["ansible.builtin.set_fact"]["_deploy_fqdn"]
    unrouted = {"app_config": {"routing": {"proxy": "none"}}, "homelabinfra_infra": {}, "instance": "pg"}
    unrouted["_deploy_domain"] = render(domain, **unrouted)
    assert render(fqdn, **unrouted) == "pg"
    routed = {"app_config": {"routing": {}}, "homelabinfra_infra": {"domain": "example.test"},
              "instance": "app"}
    routed["_deploy_domain"] = render(domain, **routed)
    assert render(fqdn, **routed) == "app.example.test"

    wire = plays[3]
    block = next(t for t in wire["tasks"] if "block" in t)
    assert block["when"] == "_deploy_routed | bool"
    wired = {str(t.get("ansible.builtin.include_tasks")) for t in block["block"]}
    assert any("wiring/authentik.yml" in w for w in wired)
    assert any("wiring/uptime-kuma.yml" in w for w in wired)
    assert any("dns.provider" in w for w in wired) and any("reverse_proxy.provider" in w for w in wired)
    outside = [str(t.get("ansible.builtin.include_tasks", "")) for t in wire["tasks"] if "block" not in t]
    assert not any("tasks/wiring/" in o for o in outside), outside

    guest = next(t for t in wire["tasks"] if "record-app-on-guest.yml" in str(t.get("ansible.builtin.include_tasks")))
    url = guest["vars"]["record_guest_url"]
    assert render(url, _deploy_routed=False, _deploy_fqdn="db.example.test") == ""
    assert render(url, _deploy_routed=True, _deploy_fqdn="app.example.test") == "https://app.example.test"


def check_docker_client_conflict():
    """MariaDB's and Oracle's MySQL clients conflict: each Docker backend refuses the other's."""
    for engine, other, install in (
        ("mysql", "mariadb-client", "MySQL | Configure the Oracle APT repository for the host client"),
        ("mariadb", "mysql-community-client", "MariaDB | Install the host client and Python client"),
    ):
        tasks = read_yaml(f"ansible/roles/{engine}/tasks/docker.yml")
        names = [t.get("name") for t in tasks]
        probe = next(t for t in tasks if other in str(t.get("ansible.builtin.command", "")))
        refuse = tasks[names.index(probe["name"]) + 1]
        assert "own stack" in refuse["ansible.builtin.assert"]["fail_msg"], engine
        assert names.index(refuse["name"]) < names.index(install), engine


def main():
    for engine in ENGINES:
        check_engine(engine)
    check_deploy_unrouted()
    check_docker_client_conflict()
    print("Database backend Deploy: shims, hooks, consumer access and no route")


if __name__ == "__main__":
    main()
