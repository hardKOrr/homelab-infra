#!/usr/bin/env python3
"""Exercise WordPress's pre-restore guard and transactional native restore."""

import os
import re
import shlex
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "ansible/roles/wordpress/files/wordpress-recovery"
RESTORE_PLAYBOOK = yaml.safe_load(
    (ROOT / "ansible/playbooks/maintenance/wordpress-restore.yml").read_text()
)
RUNDECK_RESTORE = yaml.safe_load((ROOT / "rundeck/jobs/wordpress-restore.yaml").read_text())[0]
REMOVE_PLAYBOOK = yaml.safe_load((ROOT / "ansible/playbooks/apps/remove.yml").read_text())
WORDPRESS_ROLE_TASKS = yaml.safe_load((ROOT / "ansible/roles/wordpress/tasks/main.yml").read_text())


DOCKER = r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path

args=sys.argv[1:]
root=Path(os.environ['WORDPRESS_TEST_ROOT'])
case=os.environ['WORDPRESS_TEST_CASE']
if args[0]=='compose':
    if 'run' in args:
        command=args[args.index('wordpress-cli')+1:]
        with (root/'client.log').open('a') as log:
            log.write(' '.join(command)+'\n')
        if case=='wp-cli-failure' and command[:3]==['option','update','siteurl']:
            raise SystemExit(17)
        raise SystemExit(0)
    action=next(value for value in args if value in ['stop','start'])
    with (root/'compose.log').open('a') as log:
        log.write(action+'\n')
    raise SystemExit(0)

if args[0]!='run':
    raise SystemExit(90)
entrypoint=args[args.index('--entrypoint')+1]
database=root/'database-state.sql'
if entrypoint in ['mysqldump','mariadb-dump']:
    if case=='database-dump-failure':
        raise SystemExit(16)
    sys.stdout.write(database.read_text())
    raise SystemExit(0)
if entrypoint not in ['mysql','mariadb']:
    raise SystemExit(91)
if any(value.startswith('--execute=SELECT CONCAT') for value in args):
    if database.read_text().strip() not in ['', 'EMPTY']:
        print('DROP TABLE IF EXISTS `wordpress_table`;')
    raise SystemExit(0)
input_sql=sys.stdin.read()
if 'DROP TABLE IF EXISTS' in input_sql:
    assert 'SET FOREIGN_KEY_CHECKS=0;' in input_sql
    database.write_text('EMPTY\n')
    raise SystemExit(0)
if 'SNAPSHOT-DATABASE' in input_sql:
    database.write_text('PARTIAL-DATABASE\n' if case=='database-import-failure'
                        else 'SNAPSHOT-DATABASE\n')
    raise SystemExit(17 if case=='database-import-failure' else 0)
if 'ORIGINAL-DATABASE' in input_sql:
    database.write_text('ORIGINAL-DATABASE\n')
    raise SystemExit(0)
raise SystemExit(92)
'''

PBS = r'''#!/usr/bin/env python3
import os,shutil,sys
from pathlib import Path

args=sys.argv[1:]
root=Path(os.environ['WORDPRESS_TEST_ROOT'])
snapshot=os.environ['WORDPRESS_TEST_SNAPSHOT']
if args[:2]==['snapshot','list']:
    print(snapshot)
    raise SystemExit(0)
if args[:2]==['snapshot','files']:
    if args[2]!=os.environ['WORDPRESS_TEST_PRE_RESTORE_POINT']:
        raise SystemExit(94)
    raise SystemExit(0)
if args[0]=='restore':
    destination=Path(args[3])
    destination.mkdir(parents=True,exist_ok=True)
    source=root/('snapshot-database' if args[2]=='database.pxar' else 'snapshot-content')
    shutil.copytree(source,destination,dirs_exist_ok=True)
    raise SystemExit(0)
raise SystemExit(93)
'''


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/wp-login.php" else 404)
        self.end_headers()

    def log_message(self, *_args):
        pass


def write_executable(path, content):
    path.write_text(content)
    path.chmod(0o700)


def check_playbook_contract():
    assert any(
        "pre_restore_point" in str(task.get("ansible.builtin.assert", {}).get("that", []))
        and "length > 0" in str(task.get("ansible.builtin.assert", {}).get("that", []))
        for task in RESTORE_PLAYBOOK[0]["pre_tasks"]
    )
    restore_task = next(
        task for task in RESTORE_PLAYBOOK[1]["tasks"]
        if task.get("name") == "Run the destructive WordPress restore"
    )
    argv = restore_task["ansible.builtin.command"]["argv"]
    assert "--pre-restore-point" in argv
    assert "{{ pre_restore_point }}" in argv
    assert restore_task["register"] == "wordpress_restore_result"
    assert any(option["name"] == "pre_restore_point" for option in RUNDECK_RESTORE["options"])
    assert 'pre_restore_point=${RD_OPTION_PRE_RESTORE_POINT}' in str(RUNDECK_RESTORE["sequence"])


def check_admin_email_validation():
    assertions = WORDPRESS_ROLE_TASKS[0]["ansible.builtin.assert"]["that"]
    expression = next(item for item in assertions if "admin_email" in item)
    match = re.search(r"match\('(.+)'\)", expression)
    assert match, "administrator email validation must use a readable regex"
    pattern = match.group(1)
    assert re.match(pattern, "admin@example.invalid")
    assert re.match(pattern, "admin+canary@example.invalid")
    assert not re.match(pattern, "admin @example.invalid")
    assert not re.match(pattern, "admin@example invalid")
    assert not re.match(pattern, "not-an-email")


def check_wordpress_data_directory_access():
    task = next(
        item for item in WORDPRESS_ROLE_TASKS
        if item.get("name") == "WordPress | Create project and durable data directories"
    )
    file_task = task["ansible.builtin.file"]
    assert file_task["owner"] == "{{ item.owner }}"
    assert file_task["group"] == "{{ item.group }}"
    data_directory = next(item for item in task["loop"] if "app_config.app.data_path" in item["path"])
    assert data_directory["owner"] == "33"
    assert data_directory["group"] == "33"
    assert data_directory["mode"] == "0750"


def check_remove_contract():
    pre_tasks = REMOVE_PLAYBOOK[0]["pre_tasks"]
    resolve = next(task for task in pre_tasks
                   if task.get("name") == "Resolve WordPress's named database removal inputs")
    assert "wordpress" in str(resolve["when"])
    assert "delete_data" in str(resolve["when"])
    validate = next(task for task in pre_tasks
                    if task.get("name") == "Validate WordPress's named database removal inputs")
    requirements = validate["ansible.builtin.assert"]["that"]
    assert any("provider" in item and "mariadb" in item for item in requirements)
    assert any("database_config.name" in item and "is match" in item for item in requirements)
    assert any("database_config.role" in item and "is match" in item for item in requirements)
    assert any("database_backend.client_hosts" in item for item in requirements)
    consumers = next(task for task in pre_tasks
                     if task.get("name") == "Check WordPress's database and role are not shared by another app")
    assert "database_name" in str(consumers["ansible.builtin.set_fact"])
    assert "database_user" in str(consumers["ansible.builtin.set_fact"])
    assert "rejectattr('key', 'equalto', instance)" in str(consumers["ansible.builtin.set_fact"])
    remove = next(task for task in REMOVE_PLAYBOOK[1]["tasks"]
                  if task.get("name") == "Remove WordPress's named database and role")
    assert "_rm_delete_data" in str(remove["when"])
    operations = remove["block"]
    drop_database = next(task for task in operations
                         if task.get("name") == "Drop WordPress's named MariaDB database")
    assert drop_database["community.mysql.mysql_db"]["state"] == "absent"
    drop_role = next(task for task in operations
                     if task.get("name") == "Drop WordPress's database login on permitted client networks")
    assert drop_role["community.mysql.mysql_user"]["state"] == "absent"
    assert drop_role["loop"] == "{{ hostvars['localhost']._rm_wordpress_database_backend.client_hosts }}"


def run_restore(case):
    with tempfile.TemporaryDirectory(prefix="wordpress-recovery-test-") as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_executable(bin_dir / "docker", DOCKER)
        write_executable(bin_dir / "proxmox-backup-client", PBS)

        project = root / "project"
        project.mkdir()
        data = root / "data"
        content = data / "wp-content"
        content.mkdir(parents=True)
        (content / "original.txt").write_text("original media state\n")
        original_inode = content.stat().st_ino
        original_mode = content.stat().st_mode
        (root / "database-state.sql").write_text("ORIGINAL-DATABASE\n")

        snapshot = root / "snapshot-content"
        snapshot.mkdir()
        (snapshot / "restored.txt").write_text("recovered media state\n")
        (snapshot / "uploads").mkdir()
        (snapshot / "uploads" / "canary.png").write_bytes(b"fixture image bytes\x00\xff\n")
        snapshot_database = root / "snapshot-database"
        snapshot_database.mkdir()
        (snapshot_database / "database.sql").write_text(
            "USE `source_wordpress`;\nSNAPSHOT-DATABASE\n"
        )

        now = datetime.now(timezone.utc).replace(microsecond=0)
        pre_restore_point = f"host/wordpress/{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        # The selected point must differ from the fresh safety point.
        snapshot_id = f"host/wordpress/{(now - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        config = root / "wordpress.env"
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        values = {
            "WORDPRESS_PROJECT_DIR": str(project),
            "WORDPRESS_SERVICE": "wordpress",
            "WORDPRESS_DATA_PATH": str(data),
            "WORDPRESS_CONTENT_PATH": str(content),
            "WORDPRESS_FQDN": "wordpress.example.invalid",
            "WORDPRESS_PORT": str(server.server_port),
            "WORDPRESS_CLI_IMAGE": "wordpress-cli:test",
            "WORDPRESS_ADMIN_USER": "admin",
            "WORDPRESS_ADMIN_EMAIL": "admin@example.invalid",
            "WORDPRESS_ADMIN_NAME": "Fixture Admin",
            "WORDPRESS_ADMIN_PASSWORD": "fixture-only",
            "WORDPRESS_DB_HOST": "127.0.0.1",
            "WORDPRESS_DB_PORT": "3306",
            "WORDPRESS_DB_NAME": "wordpress",
            "WORDPRESS_DB_USER": "wordpress",
            "WORDPRESS_DB_PASSWORD": "fixture-only",
            "WORDPRESS_DB_CLIENT_IMAGE": "mariadb:test",
            "PBS_REPOSITORY": "test:store",
            "PBS_FINGERPRINT": "test",
            "PBS_PASSWORD": "fixture-only",
            "PBS_BACKUP_ID": "wordpress",
            "PBS_RETENTION_DAYS": "14",
        }
        config.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
        environment = os.environ | {
            "WORDPRESS_RECOVERY_CONFIG": str(config),
            "WORDPRESS_TEST_CASE": case,
            "WORDPRESS_TEST_ROOT": str(root),
            "WORDPRESS_TEST_SNAPSHOT": snapshot_id,
            "WORDPRESS_TEST_PRE_RESTORE_POINT": pre_restore_point,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }
        try:
            result = subprocess.run(
                [str(RECOVERY), "--restore", "--snapshot", snapshot_id,
                 "--pre-restore-point", pre_restore_point],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        compose_actions = (root / "compose.log").read_text().splitlines() if (root / "compose.log").exists() else []
        success = case == "success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert content.is_dir()
        if success:
            assert not (content / "original.txt").exists()
            assert (content / "restored.txt").read_text() == "recovered media state\n"
            assert (content / "uploads" / "canary.png").read_bytes() == b"fixture image bytes\x00\xff\n"
            assert content.stat().st_ino != original_inode
            assert (root / "database-state.sql").read_text() == "SNAPSHOT-DATABASE\n"
            assert compose_actions == ["stop", "start"], compose_actions
        else:
            assert (content / "original.txt").read_text() == "original media state\n"
            assert not (content / "restored.txt").exists()
            assert content.stat().st_ino == original_inode, "rollback must rename the original tree back"
            assert content.stat().st_mode == original_mode
            assert (root / "database-state.sql").read_text() == "ORIGINAL-DATABASE\n"
            if case == "database-dump-failure":
                assert compose_actions == ["stop", "start"], compose_actions
            else:
                assert "WordPress rollback restored the previous content and database." in result.stderr
                assert compose_actions == ["stop", "stop", "start"], compose_actions
        assert not list(data.glob("wp-content.pre-restore.*")), "completed restore must remove its rollback tree"


def main():
    check_playbook_contract()
    check_admin_email_validation()
    check_wordpress_data_directory_access()
    check_remove_contract()
    run_restore("success")
    run_restore("database-dump-failure")
    run_restore("database-import-failure")
    run_restore("wp-cli-failure")
    print("WordPress: scoped Remove, fresh safety point, restore and content/database rollback passed")


if __name__ == "__main__":
    main()
