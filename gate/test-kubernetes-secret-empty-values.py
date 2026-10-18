#!/usr/bin/env python3
"""Keep empty Kubernetes Secret values as present environment variables."""

import base64
import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ANSIBLE_CONFIG", str(ROOT / "ansible/ansible.cfg"))


def main():
    tasks = yaml.safe_load(
        (ROOT / "ansible/tasks/kubernetes/sync-secret.yml").read_text(encoding="utf-8")
    )
    task = next(
        item
        for item in tasks
        if item["name"] == "k8s secret | Write the runtime copy of the canonical credentials"
    )
    template = task["ansible.builtin.command"]["stdin"]
    rendered = Templar(
        loader=DataLoader(),
        variables={
            "k8s_secret_name": "hi-events-env",
            "k8s_namespace": "app-hi-events",
            "instance": "hi-events",
            "k8s_secret_data": {
                "VITE_STRIPE_PUBLISHABLE_KEY": "",
                "fixture": "secret-value",
            },
        },
    ).template(template)
    secret = yaml.safe_load(rendered)

    assert secret["data"]["VITE_STRIPE_PUBLISHABLE_KEY"] == ""
    assert base64.b64decode(secret["data"]["fixture"]).decode() == "secret-value"
    print("Kubernetes Secret rendering preserves present empty values")


if __name__ == "__main__":
    main()
