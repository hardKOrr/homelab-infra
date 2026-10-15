#!/usr/bin/env python3
"""Exercise Mautic's paired MariaDB/PBS archive and post-move rollback."""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/mautic.yml").read_text())
catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())
backup_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/backup.yml").read_text())
restore_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/restore.yml").read_text())
deploy_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/main.yml").read_text())
remove_source = yaml.safe_load((repo / "ansible/playbooks/apps/remove.yml").read_text())


def tasks_recursive(tasks):
    for task in tasks:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from tasks_recursive(task.get(key, []))


def fixture_tasks(tasks, root, case):
    """Run production filesystem and rollback tasks with fake PBS, SQL and Compose."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        if task.get("name") == "Mautic restore | Load the target domain declaration":
            task["ansible.builtin.include_vars"]["file"] = str(root / "no-lab-config.yml")
        if task.get("name") == "Mautic restore | Create a private database client file":
            # The production play runs as root on the Docker host. Keep the mode check,
            # but let this local fixture write as the unprivileged gate user.
            task["ansible.builtin.copy"].pop("owner", None)
            task["ansible.builtin.copy"].pop("group", None)
        if task.get("name") == "Mautic restore | Converge the target database connection and URL":
            # The production play runs as root on the Docker host. Keep the mode check,
            # but let this local fixture write as the unprivileged gate user.
            task["ansible.builtin.lineinfile"].pop("owner", None)
            task["ansible.builtin.lineinfile"].pop("group", None)
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "pbs-user@pbs.example.invalid:archive",
                "k8s_pbs_fingerprint": "pbs-fingerprint-secret",
                "k8s_pbs_password": "pbs-password-secret",
            }
        if task.get("ansible.builtin.include_tasks", "").endswith(
            "fail-with-scrubbed-output.yml"
        ):
            task["ansible.builtin.include_tasks"] = str(
                repo / "ansible/tasks/fail-with-scrubbed-output.yml"
            )
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"], *compose["services"]]
            }
            task["changed_when"] = True
        if "ansible.builtin.uri" in task:
            task.pop("ansible.builtin.uri")
            for key in ["register", "until", "retries", "delay"]:
                task.pop(key, None)
            task["ansible.builtin.command"] = {"argv": [str(root / "bin/health")]}
            task["changed_when"] = False
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root, case)
    return tasks


def make_stubs(root):
    (root / "bin").mkdir()
    scripts = {
        "docker": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['MAUTIC_TEST_CASE']
root=Path(os.environ['MAUTIC_TEST_ROOT'])
script=args[-1]
volumes=[args[i+1] for i,value in enumerate(args[:-1]) if value=='--volume']
if 'proxmox-backup-client snapshot files' in script:
    assert os.environ['SNAPSHOT']=='host/mautic/2026-10-14T01:00:00Z'
    raise SystemExit(0)
if 'proxmox-backup-client restore' in script:
    assert case in ['success','database-failure-after-move']
    mount=next(value for value in volumes if value.endswith(':/restore'))
    scratch=Path(mount[:-len(':/restore')])
    archive=scratch/'archive'
    (archive/'database').mkdir(parents=True)
    (archive/'database'/'mautic.sql').write_text('CREATE TABLE recovered;\n-- recovered database\n')
    data=archive/'mautic'
    (data/'config').mkdir(parents=True)
    (data/'media'/'files').mkdir(parents=True)
    (data/'media'/'images').mkdir(parents=True)
    (data/'config'/'local.php').write_text("<?php\nreturn [\n  'db_host' => 'old-db',\n  'db_port' => '3306',\n  'db_name' => 'mautic',\n  'db_user' => 'mautic',\n  'db_password' => 'old-password',\n  'site_url' => 'https://old.example.invalid',\n  'default_timezone' => 'Pacific/Honolulu',\n);\n")
    (data/'media'/'files'/'recovered-contact.txt').write_text('recovered contact asset\n')
    raise SystemExit(0)
if 'mariadb-dump' in script:
    mount=next(value for value in volumes if value.endswith(':/work'))
    scratch=Path(mount[:-len(':/work')])
    (scratch/'database-before.sql').write_text('CREATE TABLE original;\n-- original database\n')
    raise SystemExit(0)
if 'sed' in script and 'DB_DUMP' in script:
    dump=os.environ['DB_DUMP']
    if dump.endswith('database-before.sql'):
        (root/'db-state').write_text('original database\n')
        raise SystemExit(0)
    (root/'db-state').write_text('partial recovered database\n'
                                 if case=='database-failure-after-move'
                                 else 'recovered database\n')
    raise SystemExit(7 if case=='database-failure-after-move' else 0)
raise SystemExit(0)
''',
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sorted(sys.argv[2:])==['mautic_cron','mautic_web','mautic_worker'],sys.argv
with Path(os.environ['MAUTIC_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
''',
        "health": "#!/bin/sh\nexit 0\n",
    }
    for name, script in scripts.items():
        path = root / "bin" / name
        path.write_text(script)
        path.chmod(0o700)


def run_restore(case):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_stubs(root)
        data = root / "data"
        (data / "config").mkdir(parents=True)
        (data / "media" / "files").mkdir(parents=True)
        (data / "media" / "images").mkdir(parents=True)
        (data / "config" / "local.php").write_text(
            "<?php\nreturn [\n  'default_timezone' => 'UTC',\n);\n"
        )
        (data / "media" / "files" / "original.txt").write_text("original media\n")
        (root / "db-state").write_text("original database\n")
        variables = {
            "instance": "mautic",
            "restore_target": "mautic",
            "restore_destination": "existing",
            "restore_snapshot": "host/mautic/2026-10-14T00:00:00Z",
            "restore_pre_restore_point": "host/mautic/2026-10-14T01:00:00Z",
            "restore_backup_id": "mautic",
            "restore_target_backup_id": "mautic",
            "restore_overwrite": True,
            "mautic_project_dir": str(root / "project"),
            "homelabinfra_infra": {
                "domain": "example.invalid",
                "backups": {},
                "databases": {
                    "mariadb-mautic": {
                        "provider": "mariadb", "host": "db.example.invalid", "port": 3306
                    }
                },
            },
            "homelabinfra_vault": {
                "apps": {"mautic": {"database_user": "mautic", "database_password": "test-secret"}}
            },
            "restore_source_database": {
                "provider": "mariadb", "instance": "mariadb-mautic", "name": "mautic",
                "host": "db.example.invalid", "port": 3306,
            },
            "app_config": {
                "app": {
                    "database": {"provider": "mariadb", "instance": "mariadb-mautic",
                                 "name": "mautic", "role": "mautic"},
                    "database_host": "db.example.invalid", "database_port": 3306,
                    "database_user": "mautic", "database_password": "test-secret",
                    "data_path": str(data), "port": 8080,
                },
                "routing": {"subdomain": "mautic"},
                "backup": {
                    "enabled": True, "application_consistent": True,
                    "client": {"image": "test", "key_url": "test", "repository": "test",
                               "suite": "test", "component": "test"},
                },
            },
        }
        playbook = root / "run.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "vars": variables,
            "tasks": fixture_tasks(restore_source, root, case),
        }]))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "MAUTIC_TEST_CASE": case, "MAUTIC_TEST_ROOT": str(root),
        }
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
            env=env, text=True, capture_output=True, check=False,
        )
        success = case == "success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        if success:
            assert (data / "config" / "local.php").read_text().find(
                "default_timezone' => 'Pacific/Honolulu'"
            ) >= 0
            assert (data / "media" / "files" / "recovered-contact.txt").read_text() == (
                "recovered contact asset\n"
            )
            assert (root / "db-state").read_text() == "recovered database\n"
        else:
            assert (data / "config" / "local.php").read_text().find(
                "default_timezone' => 'UTC'"
            ) >= 0
            assert (data / "media" / "files" / "original.txt").read_text() == "original media\n"
            assert not (data / "media" / "files" / "recovered-contact.txt").exists()
            assert (root / "db-state").read_text() == "original database\n"
        assert "test-secret" not in result.stdout + result.stderr
        assert not list(data.parent.glob(".mautic-mautic-restore-*")), result.stdout + result.stderr
        states = (root / "services").read_text().splitlines()
        assert states[0] == "stopped" and states[-1] == ("present" if success else "stopped"), states


def static_contract():
    config = defaults["mautic_defaults"]
    assert config["recovery"]["methods"] == ["native"]
    assert config["backup"]["application_consistent"] is True
    assert {"backup", "restore"} <= set(catalog["applications"]["mautic"]["actions"])

    deploy_tasks = list(tasks_recursive(deploy_source))
    seed_config = next(task for task in deploy_tasks
                       if task.get("name") == "Mautic | Seed configuration on first deploy")
    converge_config = next(task for task in deploy_tasks
                           if task.get("name") == "Mautic | Converge platform-owned configuration fields")
    for config_task, module in [(seed_config, "ansible.builtin.template"),
                                (converge_config, "ansible.builtin.lineinfile")]:
        assert config_task[module]["owner"] == "www-data"
        assert config_task[module]["group"] == "www-data"
        assert config_task[module]["mode"] == "0640"
    deploy_names = [task.get("name") for task in deploy_tasks]
    start_web = next(task for task in deploy_tasks
                     if task.get("name") == "Mautic | Start web service")
    verify_runtime = next(task for task in deploy_tasks
                          if task.get("name") == "Mautic | Verify the web runtime can access its configuration")
    wait_health = next(task for task in deploy_tasks
                       if task.get("name") == "Mautic | Wait for health endpoint")
    start_background = next(task for task in deploy_tasks
                            if task.get("name") == "Mautic | Start background services")
    assert start_web["community.docker.docker_compose_v2"]["services"] == ["mautic_web"]
    runtime_check = " ".join(verify_runtime["ansible.builtin.command"]["argv"])
    assert "--user www-data" in runtime_check
    assert "test -r /var/www/html/config/local.php" in runtime_check
    assert "test -w /var/www/html/config/local.php" in runtime_check
    assert start_background["community.docker.docker_compose_v2"]["services"] == [
        "mautic_cron", "mautic_worker"
    ]
    assert deploy_names.index(verify_runtime["name"]) < deploy_names.index(wait_health["name"])
    assert deploy_names.index(wait_health["name"]) < deploy_names.index(start_background["name"])

    backup_tasks = list(tasks_recursive(backup_source))
    backup_dump = next(task for task in backup_tasks
                       if task.get("name") == "Mautic backup | Dump the stopped instance's MariaDB database")
    archive = next(task for task in backup_tasks
                   if task.get("name") == "Mautic backup | Push one database and file archive to PBS")
    assert "mariadb-dump" in backup_dump["ansible.builtin.command"]["argv"][-1]
    archive_script = archive["ansible.builtin.command"]["argv"][-1]
    assert "data.pxar:/work/payload" in archive_script
    assert "--keep-last 2" in archive_script
    assert "--keep-daily" not in archive_script
    assert "mautic_config_file" in str(backup_source)
    assert "mautic_media_files_dir" in str(backup_source)
    assert "mautic_media_images_dir" in str(backup_source)

    restore_tasks = list(tasks_recursive(restore_source))
    restore_config = next(task for task in restore_tasks
                          if task.get("name") == "Mautic restore | Converge the target database connection and URL")
    assert restore_config["ansible.builtin.lineinfile"]["owner"] == "www-data"
    assert restore_config["ansible.builtin.lineinfile"]["group"] == "www-data"
    assert restore_config["ansible.builtin.lineinfile"]["mode"] == "0640"
    client_file = next(task for task in restore_tasks
                       if task.get("name") == "Mautic restore | Create a private database client file")
    assert client_file["ansible.builtin.copy"]["owner"] == "root"
    assert client_file["ansible.builtin.copy"]["mode"] == "0600"
    move = next(task for task in restore_tasks
                if task.get("name") == "Mautic restore | Move the original data directory aside")
    put_back = next(task for task in restore_tasks
                    if task.get("name") == "Mautic restore | Put the original file tree back")
    assert move["ansible.builtin.command"]["argv"][0] == "mv"
    assert put_back["ansible.builtin.command"]["argv"][0] == "mv"
    assert "_mautic_restore_database_mutated" in str(restore_source)
    assert "database-before.sql" in str(restore_source)

    removal = [
        task
        for play in remove_source
        for section in ["pre_tasks", "tasks"]
        for task in tasks_recursive(play.get(section, []))
    ]
    drop_db = next(task for task in removal
                   if task.get("name") == "Drop Mautic's named MariaDB database")
    drop_user = next(task for task in removal
                     if task.get("name") == "Drop Mautic's database login on permitted client networks")
    assert drop_db["community.mysql.mysql_db"]["state"] == "absent"
    assert drop_user["community.mysql.mysql_user"]["state"] == "absent"


def main():
    static_contract()
    run_restore("success")
    run_restore("database-failure-after-move")
    print("Mautic: one-archive recovery and post-move database/file rollback passed")


if __name__ == "__main__":
    main()
