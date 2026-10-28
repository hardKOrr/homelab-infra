"""Replace an include of the shared PBS client task files with direct fake-client calls.

gate/test-pbs-client.py proves the shared files themselves (tmpfs, pinned install, point
identity, preview). An application test only needs the client's effect on its own flow, so
it swaps the include for calls to its fake `proxmox-backup-client`:

- pbs-client-restore.yml with staging paths -> `restore <point> <archive> <path>` per archive
- pbs-client-restore.yml without paths      -> `snapshot list` (the preview)
- pbs-client-backup.yml                     -> `backup <archive>:<path> ...`, plus the
                                               role's quiesced hook and service stops are
                                               left to the application test
"""


def stub_pbs_client(task, client):
    """Rewrite `task` in place when it includes a shared PBS client file; return True if so."""
    include = task.get("ansible.builtin.include_tasks", "")
    name = include.rsplit("/", 1)[-1]
    if name not in ("pbs-client-restore.yml", "pbs-client-backup.yml"):
        return False
    variables = task.pop("vars", {})
    del task["ansible.builtin.include_tasks"]
    archives = variables["pbs_client_archives"]
    if name == "pbs-client-backup.yml":
        task["ansible.builtin.command"] = {
            "argv": [str(client), "backup", *[f"{a['name']}:{a['path']}" for a in archives]]
        }
    elif all("path" in archive for archive in archives):
        task["ansible.builtin.command"] = {
            "argv": [str(client), "restore", "{{ restore_snapshot }}", "{{ item.name }}",
                     "{{ item.path }}"]
        }
        task["loop"] = archives
    else:
        task["ansible.builtin.command"] = {"argv": [str(client), "snapshot", "list"]}
    task["changed_when"] = True
    return True
