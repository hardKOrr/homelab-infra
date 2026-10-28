#!/usr/bin/env python3
"""Check Navidrome Restore's ownership-safe extraction and post-extract ownership order."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
source = yaml.safe_load((repo / "ansible/roles/navidrome/tasks/restore.yml").read_text())


def tasks_recursive(tasks):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            if section in task:
                yield from tasks_recursive(task[section])


def named(name):
    return next(task for task in all_tasks if task.get("name") == name)


all_tasks = list(tasks_recursive(source))


def main():
    preview = named("Navidrome restore | List or verify the recovery point without changing the application")
    assert preview["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert preview["when"] == "not (restore_overwrite | bool)"

    # The stack host is an unprivileged LXC's nested Docker: the archive's ownership,
    # permissions, ACLs and xattrs cannot be applied there, so the role assigns them.
    extract = named("Navidrome restore | Extract the selected archive into staging")
    assert extract["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    args = extract["vars"]["pbs_client_restore_args"]
    for option in ["--allow-existing-dirs", "--ignore-acls", "--ignore-ownership",
                   "--ignore-permissions", "--ignore-xattrs"]:
        assert args[args.index(option) + 1] == "true", option

    staging = named("Navidrome restore | Create private staging space")
    assert staging["ansible.builtin.tempfile"]["path"] == "{{ app_config.app.data_path | dirname }}"

    ownership = named("Navidrome restore | Set data ownership for the container user")
    mode = named("Navidrome restore | Normalize the data directory mode")
    start = named("Navidrome restore | Start the named service")
    assert all_tasks.index(extract) < all_tasks.index(ownership) < all_tasks.index(mode)
    assert all_tasks.index(mode) < all_tasks.index(start)
    assert ownership["ansible.builtin.file"]["recurse"] is True
    assert mode["ansible.builtin.file"]["path"] == "{{ app_config.app.data_path }}"
    assert mode["ansible.builtin.file"]["mode"] == "0750"
    assert mode["ansible.builtin.file"].get("recurse", False) is False

    print("Navidrome Restore: ownership-safe extraction and ownership order passed")


if __name__ == "__main__":
    main()
