#!/usr/bin/env python3
"""Check Open WebUI's release image and Docker bootstrap before deployment."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def open_webui_release_image_and_docker_bootstrap():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/open-webui.yml").read_text(encoding="utf-8")
    )
    assert (
        defaults["open_webui_defaults"]["app"]["image"]
        == "ghcr.io/open-webui/open-webui:latest"
    )

    plays = yaml.safe_load(
        (ROOT / "ansible/playbooks/apps/open-webui.yml").read_text(encoding="utf-8")
    )
    deploy = next(play for play in plays if play.get("name") == "Open WebUI | Deploy")
    tasks = deploy["tasks"]

    bootstrap = task_named(tasks, "Bootstrap Docker guest")
    updates = task_named(tasks, "Configure OS update notifications")
    docker = task_named(tasks, "Ensure Docker is installed on the AI stack host")
    app = task_named(tasks, "Deploy Open WebUI")

    assert tasks.index(bootstrap) < tasks.index(updates) < tasks.index(docker) < tasks.index(app)
    assert docker["ansible.builtin.include_role"]["name"] == "docker"


if __name__ == "__main__":
    open_webui_release_image_and_docker_bootstrap()
    print("ok Open WebUI release image and stack host Docker bootstrap")
