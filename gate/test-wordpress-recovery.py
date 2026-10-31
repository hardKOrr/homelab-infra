#!/usr/bin/env python3
"""Check WordPress's restore wiring and hook, and exercise its backup helper with shims.

gate/test-restore-swap.py proves the swap and its database rollback;
gate/test-pre-restore-point-age.py proves the one-hour pre-restore point rule.
"""

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import run_hook, swap_inputs


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "ansible/roles/wordpress/files/wordpress-recovery"
RESTORE_PLAYBOOK = yaml.safe_load(
    (ROOT / "ansible/playbooks/maintenance/wordpress-restore.yml").read_text()
)
RUNDECK_RESTORE = yaml.safe_load((ROOT / "rundeck/jobs/wordpress-restore.yaml").read_text())[0]
WORDPRESS_ROLE_TASKS = yaml.safe_load((ROOT / "ansible/roles/wordpress/tasks/main.yml").read_text())
WORDPRESS_COMPOSE = (ROOT / "ansible/roles/wordpress/templates/docker-compose.yml.j2").read_text()


# Logs `compose <action>` for Compose calls, the wp-cli command after `wordpress-cli`, and
# `db <action>` for the shared database client.
DOCKER = r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path

args=sys.argv[1:]
root=Path(os.environ['WORDPRESS_TEST_ROOT'])
log=(root/'docker.log').open('a')
if args[0]=='compose':
    if 'run' in args:
        command=args[args.index('wordpress-cli')+1:]
        log.write('wp '+' '.join(command)+'\n')
        missing=os.environ.get('WORDPRESS_TEST_CASE')=='no-admin'
        raise SystemExit(1 if missing and command[:2]==['user','get'] else 0)
    log.write('compose '+next(value for value in args if value in ['stop','start'])+'\n')
    raise SystemExit(0)
assert args[0]=='run' and args[-3]=='/usr/local/bin/database-client.sh'
assert os.environ['DB_ENGINE']=='mariadb' and os.environ['DB_NAME']=='wordpress'
work=next(args[i+1] for i,value in enumerate(args) if value=='-v' and args[i+1].endswith(':/work'))
log.write('db '+args[-2]+'\n')
(Path(work[:-len(':/work')])/Path(args[-1]).name).write_text('DATABASE\n')
'''

# Plays the shared PBS client script: `backup --id ID --keep-last N --keep-daily D NAME:PATH...`.
PBS = r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:3]==['backup','--id','wordpress'], args
with (Path(os.environ['WORDPRESS_TEST_ROOT'])/'docker.log').open('a') as log:
    log.write('pbs '+' '.join(args[7:])+'\n')
raise SystemExit(8 if os.environ['WORDPRESS_TEST_CASE']=='upload-failure' else 0)
'''


def write_executable(path, content):
    path.write_text(content)
    path.chmod(0o700)


def check_playbook_contract():
    assert any(
        "pre_restore_point" in str(task.get("ansible.builtin.assert", {}).get("that", []))
        and "length > 0" in str(task.get("ansible.builtin.assert", {}).get("that", []))
        for task in RESTORE_PLAYBOOK[0]["pre_tasks"]
    )
    resolve = RESTORE_PLAYBOOK[0]
    merge = next(task for task in resolve["pre_tasks"] if "app_config" in task.get("ansible.builtin.set_fact", {}))
    assert "homelabinfra_vault.apps" in merge["ansible.builtin.set_fact"]["app_config"], \
        "the restore repairs the administrator with its Vaultwarden credential"
    names = [task.get("name") for task in resolve["tasks"]]
    assert names.index("Resolve the estate this app belongs to") < names.index("Derive the published hostname")
    run = next(task for task in RESTORE_PLAYBOOK[1]["tasks"]
               if task.get("name") == "Plan or perform the WordPress restore")
    assert run["ansible.builtin.include_role"] == {"name": "wordpress", "tasks_from": "restore"}
    for name in ("app_config", "homelabinfra_infra", "homelabinfra_vault", "wordpress_restore_backup_id",
                 "wordpress_fqdn"):
        assert run["vars"][name] == "{{ hostvars['localhost'].%s }}" % name, name
    group = next(task for task in resolve["tasks"] if task.get("name") == "Resolve the source backup group")
    expression = group["ansible.builtin.set_fact"]["wordpress_restore_backup_id"]
    for variables, expected in [
        ({"instance": "wordpress", "app_config": {"backup": {"backup_id": ""}}}, "wordpress"),
        ({"instance": "wordpress", "app_config": {"backup": {"backup_id": "wp-group"}}}, "wp-group"),
        ({"instance": "wordpress", "source_instance": "blog", "app_config": {"backup": {}}}, "blog"),
        ({"instance": "wordpress", "source_backup_id": "old", "app_config": {"backup": {}}}, "old"),
    ]:
        assert render_fact(expression, variables) == expected, (variables, expected)
    notify_play = next(play for play in RESTORE_PLAYBOOK
                       if play.get("name") == "WordPress Restore | Notify")
    check = next(task for task in notify_play["tasks"]
                 if task.get("name") == "Fail if WordPress restore did not complete")
    assert "wordpress_restore_complete" in str(check["ansible.builtin.assert"]["that"])
    assert "homelab-infra-wordpress-recovery" not in str(RESTORE_PLAYBOOK), "the guest helper only backs up"
    assert any(option["name"] == "pre_restore_point" for option in RUNDECK_RESTORE["options"])
    assert 'pre_restore_point=${RD_OPTION_PRE_RESTORE_POINT}' in str(RUNDECK_RESTORE["sequence"])

    swap = swap_inputs("wordpress")
    assert swap["recovery_swap_archives"] == ["database.pxar", "content.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ wordpress_content_path }}", "source": "content", "owner": 33, "group": 33,
        "mode": "0755"}]
    assert swap["recovery_swap_backup_id"] == "{{ wordpress_restore_backup_id }}", \
        "a point from another WordPress instance"
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _wordpress_restore_target_backup_id }}"
    assert swap["recovery_swap_staging_parent"] == "{{ wordpress_project_dir }}"
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "database/database.sql"}
    restore = yaml.safe_load((ROOT / "ansible/roles/wordpress/tasks/restore.yml").read_text())
    age = next(task for task in restore if "require-recent-pre-restore-point" in
               str(task.get("ansible.builtin.include_tasks", "")))
    assert age["when"] == "restore_overwrite | bool"
    assert age["vars"]["recovery_pre_restore_backup_id"] == "{{ _wordpress_restore_target_backup_id }}"


def render_fact(expression, variables):
    """Render one set_fact expression in a real play and return the value."""
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "render.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "vars": variables,
            "tasks": [{"ansible.builtin.set_fact": {"_value": expression}},
                      {"ansible.builtin.copy": {"content": "{{ _value }}", "dest": f"{directory}/value"}}]}]))
        result = subprocess.run([str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
                                 "-i", "localhost,", "-c", "local", str(playbook)],
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return (Path(directory) / "value").read_text().strip()


def fake_docker(root):
    def stub(tasks):
        for task in tasks:
            task.pop("no_log", None)
            argv = task["ansible.builtin.command"]["argv"]
            if isinstance(argv, list):
                argv[0] = str(root / "docker")
            else:
                task["ansible.builtin.command"]["argv"] = argv.replace("['docker',", repr([str(root / "docker")])[:-1] + ",")
        return tasks
    return stub


def check_replaced_hook():
    for case, action in {"admin": "update", "no-admin": "create"}.items():
        with tempfile.TemporaryDirectory(prefix="wordpress-hook-") as directory:
            root = Path(directory)
            write_executable(root / "docker", DOCKER)
            os.environ.update(WORDPRESS_TEST_ROOT=str(root), WORDPRESS_TEST_CASE=case)
            result = run_hook("wordpress", "replaced", {
                "wordpress_project_dir": str(root), "wordpress_fqdn": "wordpress.example.invalid",
                "app_config": {"app": {"admin_user": "admin", "admin_email": "admin@example.invalid",
                                       "admin_name": "Fixture Admin", "admin_password": "fixture-only"}},
            }, fake_docker(root))
            assert result.returncode == 0, result.stdout + result.stderr
            calls = (root / "docker.log").read_text().splitlines()
            url = "https://wordpress.example.invalid --skip-plugins --skip-themes --allow-root"
            assert calls[:3] == [f"wp option update home {url}", f"wp option update siteurl {url}",
                                 "wp user get admin --field=ID --skip-plugins --skip-themes --allow-root"]
            assert calls[3].startswith(f"wp user {action} admin"), calls
            assert "--user_pass=fixture-only" in calls[3] and "--display_name=Fixture Admin" in calls[3]
            assert ("--role=administrator" in calls[3]) == (action == "create")


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


def check_wordpress_cli_uses_data_owner():
    cli_service = WORDPRESS_COMPOSE.split("  wordpress-cli:\n", 1)[1]
    assert re.search(r'(?m)^    user: ["\']?33:33["\']?$', cli_service), (
        "the WP-CLI image must run as the owner of the 33:33 mode-0750 data mount"
    )


def check_wordpress_health_redirects():
    health_task = next(item for item in WORDPRESS_ROLE_TASKS
                       if item.get("name") == "WordPress | Wait for login endpoint")
    health_uri = health_task["ansible.builtin.uri"]
    accepted_statuses = [200, 301, 302, 303, 307, 308]
    assert health_uri["follow_redirects"] == "none"
    assert health_uri["status_code"] == accepted_statuses
    assert all(str(status) in health_task["until"] for status in accepted_statuses)
    assert '\\"follow_location\\"=>0' in WORDPRESS_COMPOSE
    assert "$$context" in WORDPRESS_COMPOSE
    assert "$$status" in WORDPRESS_COMPOSE
    assert "$$http_response_header" in WORDPRESS_COMPOSE
    assert "[23][0-9]{2}" in WORDPRESS_COMPOSE
    swap = swap_inputs("wordpress")
    assert swap["recovery_swap_health_follow_redirects"] == "none"
    assert swap["recovery_swap_health_status"] == accepted_statuses
    assert swap["recovery_swap_health_url"].endswith("/wp-login.php")


def check_backup(case):
    with tempfile.TemporaryDirectory(prefix="wordpress-recovery-test-") as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_executable(bin_dir / "docker", DOCKER)
        write_executable(bin_dir / "pbs-client.sh", PBS)
        (root / "project").mkdir()
        content = root / "data/wp-content"
        content.mkdir(parents=True)
        config = root / "wordpress.env"
        values = {
            "WORDPRESS_PROJECT_DIR": str(root / "project"),
            "WORDPRESS_SERVICE": "wordpress",
            "WORDPRESS_CONTENT_PATH": str(content),
            "WORDPRESS_DB_HOST": "127.0.0.1",
            "WORDPRESS_DB_PORT": "3306",
            "WORDPRESS_DB_NAME": "wordpress",
            "WORDPRESS_DB_USER": "wordpress",
            "WORDPRESS_DB_PASSWORD": "fixture-only",
            "WORDPRESS_DB_PROVIDER": "mariadb",
            "WORDPRESS_DB_CLIENT_IMAGE": "mariadb:test",
            "DATABASE_CLIENT_SCRIPT": "/usr/local/lib/homelab-infra/database-client.sh",
            "PBS_CLIENT_SCRIPT": str(bin_dir / "pbs-client.sh"),
            "PBS_CLIENT_CONFIG": str(root / "pbs-client.env"),
            "PBS_BACKUP_ID": "wordpress",
            "PBS_RETENTION_DAYS": "14",
        }
        config.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
        template = (ROOT / "ansible/roles/wordpress/templates/wordpress-recovery.env.j2").read_text()
        assert sorted(re.findall(r"(?m)^([A-Z_]+)=", template)) == sorted(values), \
            "the guest env holds what the backup helper reads, and no administrator credential"
        environment = os.environ | {
            "WORDPRESS_RECOVERY_CONFIG": str(config),
            "WORDPRESS_TEST_CASE": case,
            "WORDPRESS_TEST_ROOT": str(root),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }
        result = subprocess.run([str(RECOVERY), "--backup"], env=environment, text=True,
                                capture_output=True, check=False)
        assert (result.returncode == 0) == (case == "success"), result.stdout + result.stderr
        calls = (root / "docker.log").read_text().splitlines()
        assert calls[0] == "compose stop" and calls[1] == "db dump" and calls[-1] == "compose start", calls
        assert calls[2].startswith("pbs database.pxar:/var/tmp/homelab-wordpress.")
        assert calls[2].endswith(f" content.pxar:{content}")
        for mode in ("--restore", "--plan"):
            assert subprocess.run([str(RECOVERY), mode], env=environment, capture_output=True).returncode == 2


def main():
    check_playbook_contract()
    check_admin_email_validation()
    check_wordpress_data_directory_access()
    check_wordpress_cli_uses_data_owner()
    check_wordpress_health_redirects()
    check_replaced_hook()
    check_backup("success")
    check_backup("upload-failure")
    print("WordPress: restore through the shared swap, target identity repair and backup helper passed")


if __name__ == "__main__":
    main()
