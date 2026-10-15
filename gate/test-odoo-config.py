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
        (ROOT / "ansible/roles/odoo/tasks/main.yml").read_text(encoding="utf-8")
    )
    task = next(task for task in tasks if task.get("name") == "Odoo | Render configuration")
    template = task["ansible.builtin.template"]

    assert (defaults["puid"], defaults["pgid"]) == (100, 101), defaults
    assert template["owner"] == "{{ app_config.app.puid }}", template
    assert template["group"] == "{{ app_config.app.pgid }}", template
    assert template["mode"] == "0400", template
    assert task.get("no_log") is True, "Odoo's rendered secrets must stay out of task output"

    print("Odoo config permissions keep secrets private and readable by the container UID")


if __name__ == "__main__":
    main()
