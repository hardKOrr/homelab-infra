#!/usr/bin/env python3
"""Exercise Karakeep's single-archive backup and check its two-directory restore swap inputs.

Checks: one-archive backup of both persistent volumes, and the two restore swap paths from
that archive with the owner Deploy also applies.
"""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from pbs_client_stub import stub_pbs_client
from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/karakeep/tasks/backup.yml").read_text())


def fixture_tasks(tasks, root):
    """Keep production path and command tasks; stub only PBS and Compose boundaries."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {"argv": [
                str(root / "bin/service"), compose["state"], *compose["services"]]}
            task["changed_when"] = True
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


def all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from all_tasks(task.get(key, []))


def environment(root, case):
    return os.environ | {
        "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_DISPLAY_OK_HOSTS": "1" if case == "success" else "0",
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "KARAKEEP_TEST_CASE": case, "KARAKEEP_TEST_ROOT": str(root),
    }


def run_play(root, tasks, variables, case):
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": fixture_tasks(tasks, root),
    }]))
    return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                          env=environment(root, case), text=True, capture_output=True,
                          check=False)


def make_stubs(root):
    (root / "bin").mkdir()
    scripts = {
        "proxmox-backup-client": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['KARAKEEP_TEST_CASE']
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
if args[0] == 'backup':
    archive_root = root/'project'/'.pbs-backup-root'
    assert args == ['backup', f'data.pxar:{archive_root}'], args
    assert sorted(p.name for p in archive_root.iterdir()) == ['data', 'meilisearch']
    raise SystemExit(7 if case=='backup-failure' else 0)
raise SystemExit('unexpected PBS client command')
''',
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sorted(sys.argv[2:]) == ['chrome','meilisearch','web'], sys.argv
root=Path(os.environ['KARAKEEP_TEST_ROOT'])
with (root/'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
''',
    }
    for name, script in scripts.items():
        path = root / "bin" / name
        path.write_text(script)
        path.chmod(0o700)


def backup_check(case):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        search = root / "meilisearch"
        data.mkdir()
        search.mkdir()
        (data / "original.sqlite").write_text("original app state\n")
        if case != "backup-missing-database":
            (data / "db.db").write_text("SQLite database\n")
        (search / "original-index").write_text("original index\n")
        variables = {
            "instance": "karakeep", "karakeep_project_dir": str(root / "project"),
            "karakeep_meilisearch_data_path": str(search), "homelabinfra_infra": {"backups": {}},
            "app_config": {
                "app": {"data_path": str(data)},
                "backup": {"enabled": True, "application_consistent": True},
            },
        }
        result = run_play(root, backup_source, variables, case)
        success = case == "backup-success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert (data / "original.sqlite").read_text() == "original app state\n"
        assert (search / "original-index").read_text() == "original index\n"
        assert not (root / "project/.pbs-backup-root").exists(), "the archive root was left behind"


def check_restore_swap():
    """Karakeep supplies both directories from its one archive; gate/test-restore-swap.py
    proves the swap, rollback and ownership."""
    swap = swap_inputs("karakeep", "restore-docker.yml")
    owner = {"owner": "{{ karakeep_data_uid }}", "group": "{{ karakeep_data_gid }}", "mode": "0750"}
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_paths"] == [
        {"path": "{{ app_config.app.data_path }}", "source": "data/data", **owner},
        {"path": "{{ karakeep_meilisearch_data_path }}", "source": "data/meilisearch", **owner,
         "allow_empty": True},
    ]
    assert swap["recovery_swap_stop_services"] == ["web", "chrome", "meilisearch"]
    assert swap["recovery_swap_compose_project"] == "{{ karakeep_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/api/health")
    # Deploy creates both directories with the same owner the restore applies.
    deploy = yaml.safe_load((repo / "ansible/roles/karakeep/tasks/main.yml").read_text())
    directories = next(t for t in deploy if t["name"] == "Karakeep | Create persistent application directories")
    assert directories["ansible.builtin.file"]["owner"] == "{{ karakeep_data_uid }}"
    assert directories["ansible.builtin.file"]["group"] == "{{ karakeep_data_gid }}"


def main():
    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/karakeep.yml").read_text())
    config = defaults["karakeep_defaults"]
    assert config["hosting"]["method"] == "docker"
    assert config["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert config["recovery"]["methods"] == ["native"]
    assert config["backup"]["application_consistent"] is True
    assert any(item["protocol"] == "TCP" and item["port"] == 80
               for item in config["firewall"]["egress"])
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())
    actions = catalog["applications"]["karakeep"]["actions"]
    assert {"backup", "restore"} <= set(actions)
    assert "rollback" not in actions
    backup_task = next(task for task in all_tasks(backup_source)
                       if task.get("name") == "Karakeep backup | Stop only this instance's services and push one data archive")
    assert backup_task["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    backup_vars = backup_task["vars"]
    assert backup_vars["pbs_client_runtime"] == "docker"
    assert backup_vars["pbs_client_stop_services"] == ["web", "chrome", "meilisearch"]
    assert backup_vars["pbs_client_backup_args"] == ["--all-file-systems", "true"]
    # One data.pxar keeps the data/ and meilisearch/ layout of every earlier point.
    archive_root = backup_vars["pbs_client_archives"][0]["path"]
    assert [m["target"] for m in backup_vars["pbs_client_backup_mounts"]] == [
        archive_root + "/data", archive_root + "/meilisearch"]
    backup_guard = next(task for task in all_tasks(backup_source)
                        if task.get("name") == "Karakeep backup | Require a non-empty SQLite database before backup")
    assert backup_guard["ansible.builtin.assert"]["that"]
    deploy = yaml.safe_load((repo / "ansible/playbooks/apps/karakeep.yml").read_text())
    firewall_play = next(play for play in deploy if play.get("name") == "Karakeep | Open network access")
    assert any("open-app-access.yml" in task.get("ansible.builtin.include_tasks", "")
               for task in firewall_play["tasks"])

    for case in ["backup-success", "backup-failure", "backup-missing-database"]:
        backup_check(case)
    check_restore_swap()
    print("Karakeep: single-archive backup and two-directory restore swap inputs passed")


if __name__ == "__main__":
    main()
