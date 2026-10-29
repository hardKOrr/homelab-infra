#!/usr/bin/env python3
"""Exercise Authentik's application-consistent PBS backup and rollback restore."""

import copy
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from pbs_client_stub import stub_pbs_client


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def tasks_recursive(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks_recursive(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks_recursive(value.get(key, []))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls += 1
        status = 503 if self.server.fail_first and self.server.calls == 1 else 204
        self.send_response(status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root, case):
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"]]
            }
        if task.get("name") in [
            "Authentik backup | Read the running Compose services",
            "Authentik restore | Inspect running Compose services for diagnostics",
        ]:
            task["ansible.builtin.command"]["argv"][0] = str(root / "bin/docker")
        if task.get("name") == "Authentik restore | Wait for the restored instance to become ready" and case == "health-failure":
            # Make the first readiness failure enter the production rescue block; the
            # rollback health probe then receives the fixture server's 204 response.
            task.pop("until", None)
            task.pop("retries", None)
            task.pop("delay", None)
        if "ansible.builtin.uri" in task and not (
            task.get("name") == "Authentik restore | Wait for the restored instance to become ready"
            and case == "health-failure"
        ):
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root, case)
    return tasks


def write_fixture_scripts(root):
    bindir = root / "bin"
    bindir.mkdir()
    scripts = {
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
with Path(os.environ['AUTHENTIK_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
''',
        "docker": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
case=os.environ['AUTHENTIK_TEST_CASE']
root=Path(os.environ['AUTHENTIK_TEST_ROOT'])
assert sys.argv[1:2] == ['compose']
if 'ps' in sys.argv:
    if case=='postgresql-down':
        services='redis\nserver\nworker'
    else:
        services='postgresql\nredis\nserver\nworker'
    (root/'running-services').write_text(services,encoding='utf-8')
    print(services)
''',
        "proxmox-backup-client": r'''#!/usr/bin/env python3
import json,os,shutil,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['AUTHENTIK_TEST_CASE']
root=Path(os.environ['AUTHENTIK_TEST_ROOT'])
if args[0]=='backup':
    if case=='backup-failure':
        print('fixture upload failed',file=sys.stderr)
        raise SystemExit(8)
    raise SystemExit(0)
if args[:2]==['snapshot','list']:
    print('host/authentik/2026-10-16T00:00:00Z')
    raise SystemExit(0)
if args[0]=='restore':
    if case=='extract-failure':
        print('fixture extraction failed',file=sys.stderr)
        raise SystemExit(9)
    destination=Path(args[3])
    shutil.copytree(root/'source-data',destination,dirs_exist_ok=True)
    raise SystemExit(0)
raise SystemExit(3)
''',
    }
    for name, content in scripts.items():
        path = bindir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


def environment(root, case):
    return os.environ | {
        "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "AUTHENTIK_TEST_CASE": case,
        "AUTHENTIK_TEST_ROOT": str(root),
        "PATH": str(root / "bin") + os.pathsep + os.environ.get("PATH", ""),
    }


def run_tasks(root, tasks, variables, case, server=None):
    if server is not None:
        variables["app_config"]["app"]["port"] = server.server_port
        server.fail_first = case == "health-failure"
        server.calls = 0
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": variables,
        "tasks": fixture_tasks(tasks, root, case),
    }]), encoding="utf-8")
    return subprocess.run(
        [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(playbook)],
        env=environment(root, case),
        cwd=ROOT / "ansible",
        text=True,
        capture_output=True,
        check=False,
    )


def make_instance_tree(path, marker):
    for name in ["database", "redis", "media", "certs", "custom-templates"]:
        (path / name).mkdir(parents=True, exist_ok=True)
    (path / "database/PG_VERSION").write_text("16\n", encoding="utf-8")
    (path / "marker.txt").write_text(marker, encoding="utf-8")


def base_variables(root, server):
    project = root / "target"
    data_path = project / "data"
    project.mkdir()
    make_instance_tree(data_path, "original")
    return {
        "instance": "authentik",
        "authentik_project_dir": str(project),
        "app_config": {
            "app": {"data_path": str(data_path), "port": server.server_port},
            "backup": {
                "enabled": True,
                "application_consistent": True,
                "retention_days": 14,
                "datastore": "",
            },
        },
        "homelabinfra_infra": {"backups": {}},
        "restore_backup_id": "authentik",
        "restore_target_backup_id": "authentik",
        "restore_snapshot": "host/authentik/2026-10-16T00:00:00Z",
        "restore_pre_restore_point": "host/authentik/2026-10-16T00:30:00Z",
        "restore_destination": "existing",
        "restore_overwrite": True,
    }


def check_contract_and_secret_continuity():
    defaults = read_yaml(ROOT / "ansible/vars/app-defaults/authentik.yml")["authentik_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2

    catalog = read_yaml(ROOT / "catalog/applications.yml")["applications"]["authentik"]
    assert set(catalog["extra"]) >= {"backup", "restore"}
    assert "rollback" in catalog["exclude"]

    main = read_yaml(ROOT / "ansible/roles/authentik/tasks/main.yml")
    main_tasks = list(tasks_recursive(main))
    install = task_named(main_tasks, "Install the pinned PBS client for Authentik recovery")
    assert install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-install.yml")
    marker = task_named(main_tasks, "Check whether this instance already has a PostgreSQL database")
    assert "{{ app_config.app.data_path }}/database/PG_VERSION" in marker["ansible.builtin.stat"]["path"]
    guard = task_named(main_tasks, "Refuse to replace credentials for an initialized database")
    guard_text = str(guard["ansible.builtin.assert"]["that"])
    assert all(field in guard_text for field in ["postgres_password", "secret_key", "admin_password", ".token"])
    secret_task = task_named(main_tasks, "Resolve secrets (reuse what exists, generate only what is missing)")
    secret_text = str(secret_task["ansible.builtin.set_fact"])
    assert "_authentik_recorded_sso.postgres_password" in secret_text
    assert "_authentik_recorded_sso.secret_key" in secret_text
    assert "_authentik_recorded_sso.admin_password" in secret_text


def check_backup_and_restore_contract():
    backup = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    backup_tasks = list(tasks_recursive(backup))
    assert "data.pxar" in str(backup)
    assert "state: stopped" in yaml.safe_dump(backup)
    assert "state: present" in yaml.safe_dump(backup)
    assert "PG_VERSION" in str(backup)
    assert "_authentik_backup_expected_services" in str(backup)
    upload = task_named(backup_tasks, "Authentik backup | Upload the complete data directory to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _authentik_backup_data_path }}"}
    ]

    restore = read_yaml(ROOT / "ansible/roles/authentik/tasks/restore.yml")
    restore_tasks = list(tasks_recursive(restore))
    assert "restore_pre_restore_point" in str(restore)
    assert "data.pxar" in str(restore)
    assert "_authentik_restore_stage" in str(restore)
    assert "_authentik_restore_previous" in str(restore)
    assert "_authentik_restore_old_data_moved" in str(restore)
    assert "Put the original data back" in str(restore)
    assert "Wait for the restored instance to become ready" in str(restore)
    assert "state: stopped" in yaml.safe_dump(restore)
    assert "state: present" in yaml.safe_dump(restore)
    assert "not (restore_overwrite | bool)" in str(restore)
    assert not any(
        task.get("name") == "Authentik restore | Require all four target services to be running"
        for task in restore_tasks
    )
    assert task_named(
        restore_tasks, "Authentik restore | Report service status without gating recovery"
    )
    preview = task_named(
        restore_tasks, "Authentik restore | List or verify the recovery point before changing Authentik"
    )
    assert preview["vars"]["pbs_client_overwrite"] is False and "when" not in preview
    extract = task_named(restore_tasks, "Authentik restore | Extract the selected PBS archive")
    assert extract["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _authentik_restore_stage.path }}/data"}
    ]

    remove = read_yaml(ROOT / "ansible/playbooks/apps/remove.yml")
    remove_tasks = list(tasks_recursive(remove))
    unwire = task_named(remove_tasks, "Unwire SSO")
    assert "is match('^authentik(?:-|$)')" in str(unwire["when"]), (
        "removing an Authentik instance must not query or mutate another SSO provider")

    target = read_yaml(ROOT / "ansible/tasks/maintenance/resolve-app-target.yml")
    target_tasks = list(tasks_recursive(target))
    stack_target = task_named(target_tasks, "Resolve app target | Resolve the estate-scoped stack host")
    assert stack_target["ansible.builtin.include_tasks"] == "../stack/resolve-existing-host.yml"
    stack_vars = stack_target["vars"]
    assert stack_vars["stack_name"] == "{{ _rat_stack }}"
    assert "routing" in stack_vars["stack_estate"] and "estate" in stack_vars["stack_estate"]
    assert stack_vars["resolve_stack_app_instance"] == "{{ _rat_instance }}"
    selected_stack = task_named(target_tasks, "Resolve app target | Select the Docker stack target host")
    assert selected_stack["ansible.builtin.set_fact"]["_rat_host"] == "{{ resolve_stack_host }}"
    selected_vm = task_named(target_tasks, "Resolve app target | Select the Docker-on-VM target host")
    assert "groups[tag_group_name]" in selected_vm["ansible.builtin.set_fact"]["_rat_host"]


def check_transactional_restore_fixtures():
    restore_tasks = read_yaml(ROOT / "ansible/roles/authentik/tasks/restore.yml")
    for case in [
        "success",
        "postgresql-down",
        "health-failure",
        "extract-failure",
    ]:
        with tempfile.TemporaryDirectory(prefix="authentik-recovery-") as tmp:
            root = Path(tmp)
            write_fixture_scripts(root)
            make_instance_tree(root / "source-data", "restored")
            server = http.server.HTTPServer(("127.0.0.1", 0), Health)
            server.fail_first = False
            server.calls = 0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                variables = base_variables(root, server)
                result = run_tasks(root, restore_tasks, variables, case, server)
                current = Path(variables["app_config"]["app"]["data_path"])
                marker = (current / "marker.txt").read_text(encoding="utf-8")
                events_path = root / "services"
                events = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
                if case in ["success", "postgresql-down"]:
                    assert result.returncode == 0, result.stdout + result.stderr
                    assert marker == "restored"
                    assert events == ["stopped", "present"]
                    if case == "postgresql-down":
                        assert (root / "running-services").read_text(encoding="utf-8") == (
                            "redis\nserver\nworker"
                        )
                else:
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert marker == "original"
                    if case == "health-failure":
                        expected_events = ["stopped", "present", "stopped", "present"]
                    else:
                        expected_events = ["stopped", "stopped", "present"]
                    assert events == expected_events
                    assert "rollback" in result.stdout.lower() or "rollback" in result.stderr.lower()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


def check_backup_restarts_the_instance():
    backup_tasks = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    for case in ["success", "backup-failure"]:
        with tempfile.TemporaryDirectory(prefix="authentik-backup-") as tmp:
            root = Path(tmp)
            write_fixture_scripts(root)
            server = http.server.HTTPServer(("127.0.0.1", 0), Health)
            server.fail_first = False
            server.calls = 0
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                variables = base_variables(root, server)
                result = run_tasks(root, backup_tasks, variables, case, server)
                data_path = Path(variables["app_config"]["app"]["data_path"])
                marker = (data_path / "marker.txt").read_text(encoding="utf-8")
                events_path = root / "services"
                events = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
                assert marker == "original"
                assert events == ["stopped", "present"]
                if case == "success":
                    assert result.returncode == 0, result.stdout + result.stderr
                else:
                    assert result.returncode != 0, result.stdout + result.stderr
                    output = result.stdout.lower() + result.stderr.lower()
                    assert "backup" in output, output
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


def main():
    check_contract_and_secret_continuity()
    check_backup_and_restore_contract()
    check_backup_restarts_the_instance()
    check_transactional_restore_fixtures()
    print("Authentik native recovery: contract, estate-scoped target, secret continuity and transactional restore passed.")


if __name__ == "__main__":
    main()
