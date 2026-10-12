#!/usr/bin/env python3
"""Keep the Home Assistant PBS restore cache on a filesystem supporting temporary files."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
RESTORE_TASKS = ROOT / "ansible/roles/home-assistant/tasks/restore.yml"


def find_task(node, name):
    if isinstance(node, dict):
        if node.get("name") == name:
            return node
        for value in node.values():
            try:
                return find_task(value, name)
            except StopIteration:
                pass
    elif isinstance(node, list):
        for value in node:
            try:
                return find_task(value, name)
            except StopIteration:
                pass
    raise StopIteration(name)


def main():
    tasks = yaml.safe_load(RESTORE_TASKS.read_text())
    extract = find_task(tasks, "Home Assistant restore | Extract the selected archive")
    argv = extract["ansible.builtin.command"]["argv"]
    tmpfs_index = argv.index("--tmpfs")
    assert argv[tmpfs_index + 1] == "/root/.cache"
    assert "/bin/sh" in argv
    assert "proxmox-backup-client restore \"$SNAPSHOT\" data.pxar /restore" in argv[-1]

    print("Home Assistant restore: PBS cache uses tmpfs for archive extraction")


if __name__ == "__main__":
    main()
