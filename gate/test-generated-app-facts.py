#!/usr/bin/env python3
"""Check dynamic app instance keys are published under their resolved names."""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def read_tasks(path):
    for document in yaml.safe_load_all((ROOT / path).read_text(encoding="utf-8")):
        for play in document if isinstance(document, list) else [document]:
            yield from play.get("tasks", [])


def named_task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def app_producers_publish_named_instance_keys():
    writer = yaml.safe_load(
        (ROOT / "ansible/tasks/bootstrap/write-generated-facts.yml").read_text(
            encoding="utf-8"
        )
    )
    normalize = named_task(writer, "Generated facts | Resolve dynamic app instance key")
    normalize_expression = normalize["ansible.builtin.set_fact"]["_generated_facts_data"]

    fixtures = (
        (
            "ansible/playbooks/apps/ollama.yml",
            "Record Ollama endpoint for named AI consumers",
            "ollama",
            "_ollama_deploy_host",
            "ollama",
            11434,
        ),
        (
            "ansible/playbooks/apps/open-webui.yml",
            "Record Open WebUI endpoint for app-to-app consumers",
            "open-webui",
            "_open_webui_host",
            "open-webui",
            8080,
        ),
    )

    for path, task_name, instance, host_var, provider, port in fixtures:
        task = named_task(list(read_tasks(path)), task_name)
        input_data = render(
            task["vars"]["generated_facts_data"],
            {
                "instance": instance,
                host_var: {
                    "ansible_host": "192.0.2.80",
                    "app_config": {"app": {"port": port}},
                },
            },
        )
        assert "{{ instance }}" in input_data, input_data
        resolved = render(
            normalize_expression,
            {"_generated_facts_data": input_data, "instance": instance},
        )
        assert "{{ instance }}" not in resolved, resolved
        assert resolved[instance] == {
            "provider": provider,
            "instance": instance,
            "host": "http://192.0.2.80",
            "port": str(port),
            "url": f"http://192.0.2.80:{port}/",
        }, resolved

    k8s_task = named_task(
        list(read_tasks("ansible/playbooks/apps/open-webui.yml")),
        "Record the Kubernetes Open WebUI endpoint for app-to-app consumers",
    )
    k8s_input = render(
        k8s_task["vars"]["generated_facts_data"],
        {
            "instance": "open-webui",
            "wiring_upstream_host": "192.0.2.80",
            "wiring_upstream_port": 80,
            "hostvars": {"localhost": {"open_webui_fqdn": "open-webui.example.test"}},
        },
    )
    k8s_resolved = render(
        normalize_expression,
        {"_generated_facts_data": k8s_input, "instance": "open-webui"},
    )
    assert k8s_resolved["open-webui"] == {
        "provider": "open-webui",
        "instance": "open-webui",
        "host": "http://192.0.2.80",
        "port": "80",
        "url": "https://open-webui.example.test/",
    }, k8s_resolved

    # A rerun merges an already-resolved entry and preserves unrelated app records.
    collision = {
        "{{ instance }}": {"provider": "ollama", "url": "http://192.0.2.80:11434/"},
        "ollama": {"instance": "ollama", "port": 11434},
        "other-app": {"provider": "forgejo"},
    }
    merged = render(
        normalize_expression,
        {"_generated_facts_data": collision, "instance": "ollama"},
    )
    assert merged == {
        "ollama": {
            "provider": "ollama",
            "url": "http://192.0.2.80:11434/",
            "instance": "ollama",
            "port": 11434,
        },
        "other-app": {"provider": "forgejo"},
    }, merged

    # Validation and every registry sink must use the normalized payload.
    consumers = (
        "Generated facts | Reject credential-shaped fields",
        "Generated facts | Merge and write facts for {{ generated_facts_service }}",
        "Generated facts | Merge {{ generated_facts_service }} into the live registry",
        "Generated facts | Publish the live registry on localhost",
    )
    for task_name in consumers:
        task = named_task(writer, task_name)
        serialized = yaml.safe_dump(task)
        assert "_generated_facts_data" in serialized, task
        assert "generated_facts_data" not in serialized.replace(
            "_generated_facts_data", ""
        ), task


if __name__ == "__main__":
    app_producers_publish_named_instance_keys()
    print("ok generated app fact instance keys")
