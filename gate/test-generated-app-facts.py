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

    # Each role's deploy-wire hook writes its `apps` entry from its guest's address, or
    # from the cluster route on Kubernetes (playbooks/apps/deploy.yml).
    def hook_entry(app, method):
        task = named_task(
            yaml.safe_load((ROOT / f"ansible/roles/{app}/tasks/deploy-wire.yml").read_text()),
            next(t["name"] for t in yaml.safe_load(
                (ROOT / f"ansible/roles/{app}/tasks/deploy-wire.yml").read_text())
                if "write-generated-facts.yml" in str(t.get("ansible.builtin.include_tasks"))),
        )
        port = {"ollama": 11434, "open-webui": 8080}[app]
        variables = dict(task["vars"])
        variables.update({
            "instance": app,
            "_deploy_fqdn": f"{app}.example.test",
            "groups": {f"deploy_{app}": ["guest"]},
            "hostvars": {"guest": {"ansible_host": "192.0.2.80"}},
            "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.90"}},
            "app_config": {"hosting": {"method": method}, "app": {"port": port}},
        })
        # The hook keys the entry by the instance itself, so the writer's legacy
        # "{{ instance }}" key normalization has nothing to do.
        data = render(task["vars"]["generated_facts_data"], variables)
        assert list(data) == [app], data
        return data[app]

    for app in ("ollama", "open-webui"):
        port = {"ollama": 11434, "open-webui": 8080}[app]
        got = hook_entry(app, "docker")
        assert got == {
            "provider": app,
            "instance": app,
            "host": "http://192.0.2.80",
            "port": str(port),
            "url": f"http://192.0.2.80:{port}/",
        }, got

    assert hook_entry("ollama", "kubernetes") == {
        "provider": "ollama",
        "instance": "ollama",
        "host": "https://ollama.example.test",
        "port": "443",
        "url": "https://ollama.example.test/",
    }
    k8s = hook_entry("open-webui", "kubernetes")
    assert k8s == {
        "provider": "open-webui",
        "instance": "open-webui",
        "host": "http://192.0.2.90",
        "port": "80",
        "url": "https://open-webui.example.test/",
    }, k8s

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
