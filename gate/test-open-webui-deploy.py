#!/usr/bin/env python3
"""Check Open WebUI deployment bootstrap and native recovery wiring."""

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
        == "ghcr.io/open-webui/open-webui:v0.11.4"
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


def open_webui_recovery_uploads_through_the_shared_client():
    script = (ROOT / "ansible/roles/open-webui/files/open-webui-recovery").read_text(
        encoding="utf-8"
    )
    config = (
        ROOT / "ansible/roles/open-webui/templates/open-webui-recovery.env.j2"
    ).read_text(encoding="utf-8")
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/open-webui/tasks/main.yml").read_text(encoding="utf-8")
    )

    # PBS credentials live only in the shared client's root-only settings.
    assert "PBS_CLIENT_CONFIG=" in config and "PBS_PASSWORD" not in config
    assert '"$PBS_CLIENT_SCRIPT" backup --id "$OPEN_WEBUI_BACKUP_ID" --no-prune data.pxar:' in script
    assert "PRAGMA integrity_check" in script
    install = task_named(tasks, "Open WebUI | Install the shared PBS client and this instance's settings")
    assert install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-script.yml")
    assert install["vars"]["pbs_client_runtime"] == "docker"
    render = task_named(tasks, "Open WebUI | Render root-only recovery configuration")
    assert tasks.index(install) < tasks.index(render)


if __name__ == "__main__":
    open_webui_release_image_and_docker_bootstrap()
    open_webui_recovery_uploads_through_the_shared_client()
    print("ok Open WebUI deployment bootstrap and native recovery wiring")
