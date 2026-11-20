#!/usr/bin/env python3
"""Keep Odoo's private config and data ownership aligned with its image user."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/odoo.yml").read_text(encoding="utf-8")
    )["odoo_defaults"]["app"]
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/odoo/tasks/docker.yml").read_text(encoding="utf-8")
    )
    task = next(task for task in tasks if task.get("name") == "Odoo | Deploy the Compose project")
    assert task["ansible.builtin.include_tasks"].endswith("tasks/stack/compose-deploy.yml")
    template = next(f for f in task["vars"]["compose_files"] if f["src"] == "odoo.conf.j2")

    assert (defaults["puid"], defaults["pgid"]) == (100, 101), defaults
    assert template["owner"] == "{{ app_config.app.puid }}", template
    assert template["group"] == "{{ app_config.app.pgid }}", template
    assert template["mode"] == "0400", template
    # compose-deploy.yml hides the render when compose_secrets is set.
    assert "app_config.app.database_password" in task["vars"]["compose_secrets"], \
        "Odoo's rendered secrets must stay out of task output"

    print("Odoo config permissions keep secrets private and readable by the container UID")


if __name__ == "__main__":
    main()
