#!/usr/bin/env python3
"""Render the Homepage workload and check its stateless filesystem contract."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ansible/roles/homepage/templates/manifest.yaml.j2"


def render_manifest():
    variables = {
        "instance": "homepage",
        "app_config": {
            "app": {"image": "ghcr.io/gethomepage/homepage:v1.8.0", "port": 3000}
        },
        "homepage_service_groups": [
            {"Platform": [{"name": "Kubernetes", "href": "https://k8s.example.test"}]}
        ],
        "homepage_settings": {"title": "Homelab", "theme": "dark"},
        "homepage_fqdn": "homepage.example.test",
    }
    source = TEMPLATE.read_text(encoding="utf-8")
    rendered = Templar(loader=DataLoader(), variables=variables).template(source)
    return list(yaml.safe_load_all(rendered))


def homepage_uses_ephemeral_writable_logs_with_read_only_inputs():
    documents = render_manifest()
    deployment = next(
        document
        for document in documents
        if document["kind"] == "Deployment"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}

    assert pod["securityContext"]["runAsUser"] == 1000
    assert pod["securityContext"]["fsGroup"] == 1000
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert mounts["config"]["readOnly"] is True
    assert mounts["logs"]["mountPath"] == "/app/config/logs"
    assert volumes["logs"]["emptyDir"]["sizeLimit"] == "64Mi"
    assert "persistentVolumeClaim" not in str(volumes["logs"])
    assert all(
        "persistentVolumeClaim" not in document
        for document in documents
        if document["kind"] in {"Deployment", "PersistentVolumeClaim"}
    )


if __name__ == "__main__":
    homepage_uses_ephemeral_writable_logs_with_read_only_inputs()
    print("Homepage rendering: config stays read-only and bounded logs use ephemeral storage")
