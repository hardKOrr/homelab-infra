#!/usr/bin/env python3
"""Render the Homepage workload and check its stateless filesystem contract."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ansible/roles/homepage/templates/manifest.yaml.j2"
DASHBOARD_TASKS = ROOT / "ansible/roles/homepage/tasks/dashboard.yml"


def render_expression(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def dashboard_fixture():
    presentation = {
        "title": "Homelab Homepage Drill Canary",
        "theme": "dark",
        "color": "slate",
        "platform_group": "Platform Drill",
        "media_group": "Media Drill",
        "layout": {},
    }
    variables = {
        "app_config": {"presentation": presentation},
        "homelabinfra_infra": {
            "vaultwarden": {"host": "https://vaultwarden.example.test"},
            "sso": {"host": "https://authentik.example.test"},
            "monitoring": {"host": "https://status.example.test"},
            "media": {
                "emby": {
                    "host": "https://emby.example.test",
                    "app": "Emby",
                },
                "jellyfin": {
                    "host": "https://jellyfin.example.test",
                    "app": "Jellyfin",
                },
            },
        },
    }
    tasks = yaml.safe_load(DASHBOARD_TASKS.read_text(encoding="utf-8"))
    by_name = {task["name"]: task for task in tasks}

    def apply_set_fact(task_name):
        task = by_name[task_name]
        for name, expression in task["ansible.builtin.set_fact"].items():
            variables[name] = render_expression(expression, variables)

    def apply_loop(task_name, items):
        task = by_name[task_name]
        condition = task["when"]
        expression = next(iter(task["ansible.builtin.set_fact"].values()))
        for item in items:
            variables["item"] = item
            allowed = render_expression("{{ " + condition + " }}", variables)
            if allowed is True or str(allowed).lower() == "true":
                variables[next(iter(task["ansible.builtin.set_fact"]))] = render_expression(
                    expression, variables
                )

    apply_set_fact("Homepage | Define approved platform facts")
    apply_set_fact("Homepage | Start generated platform entries")
    apply_loop(
        "Homepage | Add platform entries with an approved endpoint",
        [
            {"key": key, "value": value}
            for key, value in variables["_homepage_fact_catalog"].items()
        ],
    )
    apply_set_fact("Homepage | Start generated media entries")
    apply_loop(
        "Homepage | Add deployed media application entries",
        [
            {"key": key, "value": value}
            for key, value in variables["homelabinfra_infra"]["media"].items()
        ],
    )
    apply_set_fact("Homepage | Assemble generated service groups")
    return presentation, variables["homepage_service_groups"], variables["homepage_settings"]


def render_manifest():
    presentation, service_groups, settings = dashboard_fixture()
    variables = {
        "instance": "homepage",
        "app_config": {
            "app": {"image": "ghcr.io/gethomepage/homepage:v1.8.0", "port": 3000},
            "presentation": presentation,
        },
        "homepage_service_groups": service_groups,
        "homepage_settings": settings,
        "homepage_fqdn": "homepage.example.test",
    }
    source = TEMPLATE.read_text(encoding="utf-8")
    rendered = render_expression(source, variables)
    return list(yaml.safe_load_all(rendered))


def homepage_renders_named_services_and_ephemeral_writable_config():
    documents = render_manifest()
    config_map = next(document for document in documents if document["kind"] == "ConfigMap")
    services = yaml.safe_load(config_map["data"]["services.yaml"])
    assert services == [
        {
            "Platform Drill": [
                {
                    "Vaultwarden": {
                        "href": "https://vaultwarden.example.test",
                        "description": "Secret store",
                    }
                },
                {
                    "Authentik": {
                        "href": "https://authentik.example.test",
                        "description": "Access and identity",
                    }
                },
                {
                    "Uptime Kuma": {
                        "href": "https://status.example.test",
                        "description": "Uptime monitoring",
                    }
                },
            ]
        },
        {
            "Media Drill": [
                {
                    "emby": {
                        "href": "https://emby.example.test",
                        "description": "Emby application",
                    }
                },
                {
                    "jellyfin": {
                        "href": "https://jellyfin.example.test",
                        "description": "Jellyfin application",
                    }
                },
            ]
        },
    ]
    settings = yaml.safe_load(config_map["data"]["settings.yaml"])
    assert settings["title"] == "Homelab Homepage Drill Canary"

    deployment = next(
        document
        for document in documents
        if document["kind"] == "Deployment"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    environment = {entry["name"]: entry["value"] for entry in container["env"]}

    assert pod["securityContext"]["runAsUser"] == 1000
    assert pod["securityContext"]["fsGroup"] == 1000
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert environment["HOMEPAGE_ALLOWED_HOSTS"] == "homepage.example.test"
    assert mounts["/app/config"]["name"] == "config-runtime"
    assert volumes["config-runtime"]["emptyDir"]["sizeLimit"] == "64Mi"
    for path, sub_path in (
        ("/app/config/services.yaml", "services.yaml"),
        ("/app/config/settings.yaml", "settings.yaml"),
    ):
        assert mounts[path]["subPath"] == sub_path
        assert mounts[path]["readOnly"] is True
    assert {document["kind"] for document in documents} == {
        "ConfigMap",
        "Deployment",
        "Service",
        "Ingress",
    }


if __name__ == "__main__":
    homepage_renders_named_services_and_ephemeral_writable_config()
    print("Homepage rendering: named service groups, allowed host, and bounded ephemeral config passed")
