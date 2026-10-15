#!/usr/bin/env python3
"""Keep Odoo's PBS restore client temp files on its writable bind mount."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/odoo/tasks/restore.yml").read_text(encoding="utf-8")
    )
    task = next(
        task for task in tasks
        if task.get("name") == "Odoo restore | Fetch and validate the paired artifact"
    )
    command = task["ansible.builtin.command"]
    argv = command["argv"]
    script = argv[-1]

    assert "{{ _odoo_restore_scratch.path }}:/restore" in argv, argv
    assert task.get("no_log") is True, "PBS credentials and repository must stay private"
    setup = [
        "install -d -m 0700 /restore/tmp /restore/home/.cache /restore/home/.config",
        "export TMPDIR=/restore/tmp",
        "export HOME=/restore/home",
        "export XDG_CACHE_HOME=/restore/home/.cache",
        "export XDG_CONFIG_HOME=/restore/home/.config",
    ]
    positions = [script.index(line) for line in setup]
    restore = script.index('proxmox-backup-client restore "$SNAPSHOT" database.pxar')
    assert positions == sorted(positions), setup
    assert positions[-1] < restore, "PBS client paths must be redirected before archive restore"

    print("Odoo PBS restore stages client temp and cache paths on the writable scratch mount")


if __name__ == "__main__":
    main()
