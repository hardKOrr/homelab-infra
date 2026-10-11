#!/usr/bin/env python3
"""Check Home Assistant's managed reverse-proxy configuration."""

import base64
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parent.parent
ROLE_TASKS = ROOT / "ansible/roles/home-assistant/tasks/main.yml"


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(source)


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def main():
    role_tasks = list(yaml.safe_load_all(ROLE_TASKS.read_text()))[0]
    proxy_resolution = task_named(role_tasks, "Home Assistant | Resolve the declared reverse proxy")
    resolve_values = proxy_resolution["ansible.builtin.set_fact"]
    assert render(
        resolve_values["_ha_reverse_proxy_provider"],
        {"homelabinfra_infra": {"reverse_proxy": {"provider": "caddy", "host": "http://192.0.2.20"}}},
    ) == "caddy"
    assert render(
        resolve_values["_ha_trusted_proxy_host"],
        {"homelabinfra_infra": {"reverse_proxy": {"host": "http://192.0.2.20:2019"}}},
    ) == "192.0.2.20"

    proxy_assertion = task_named(
        role_tasks, "Home Assistant | Assert the reverse proxy has a trusted IP address"
    )
    assert "ansible.utils.ipv4_address" in proxy_assertion["ansible.builtin.assert"]["that"][0]
    assert "_ha_reverse_proxy_provider in ['caddy', 'nginx']" == proxy_assertion["when"]

    seed = task_named(
        role_tasks, "Home Assistant | Seed default YAML configuration when a proxy is registered"
    )
    seed_options = seed["ansible.builtin.copy"]
    assert seed_options["content"] == "default_config:\n"
    assert seed_options["force"] is False
    assert "_ha_reverse_proxy_provider in ['caddy', 'nginx']" == seed["when"]

    remove_managed = task_named(
        role_tasks, "Home Assistant | Exclude the managed proxy block from authored configuration"
    )["ansible.builtin.set_fact"]["_ha_authored_configuration"]
    managed_config = (
        "default_config:\n"
        "# BEGIN ANSIBLE MANAGED HOME ASSISTANT REVERSE PROXY\n"
        "http:\n  use_x_forwarded_for: true\n"
        "# END ANSIBLE MANAGED HOME ASSISTANT REVERSE PROXY\n"
    )
    authored = render(
        remove_managed,
        {
            "_ha_configuration_file": {
                "content": base64.b64encode(managed_config.encode()).decode()
            }
        },
    )
    assert "http:" not in authored

    conflict_check = task_named(
        role_tasks, "Home Assistant | Refuse to duplicate a user-owned HTTP integration"
    )["ansible.builtin.assert"]["that"][0]
    assert render(
        "{{ (%s) }}" % conflict_check,
        {"_ha_authored_configuration": "default_config:\n"},
    ) is True
    assert render(
        "{{ (%s) }}" % conflict_check,
        {"_ha_authored_configuration": "default_config:\nhttp:\n  server_port: 8123\n"},
    ) is False

    proxy_block = task_named(
        role_tasks, "Home Assistant | Trust the registered reverse proxy"
    )
    assert proxy_block["notify"] == "Restart Home Assistant"
    rendered_block = render(
        proxy_block["ansible.builtin.blockinfile"]["block"],
        {"_ha_trusted_proxy_host": "192.0.2.20"},
    )
    assert yaml.safe_load(rendered_block) == {
        "http": {"use_x_forwarded_for": True, "trusted_proxies": ["192.0.2.20"]}
    }
    assert role_tasks.index(proxy_block) < next(
        i for i, task in enumerate(role_tasks) if task.get("name") == "Home Assistant | Start the application"
    )

    print("Home Assistant proxy: trusts only the registered proxy and preserves authored configuration")


if __name__ == "__main__":
    main()
