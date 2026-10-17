#!/usr/bin/env python3
"""Exercise Authentik's application-consistent PBS backup and rollback restore."""

import copy
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
SCRUB_FAILURE_TASK = ROOT / "ansible/tasks/fail-with-scrubbed-output.yml"


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
        if task.get("name", "").endswith("Resolve the PBS client target"):
            task.pop("ansible.builtin.include_tasks", None)
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "authentik-test@pbs.invalid:datastore",
                "k8s_pbs_fingerprint": "test-fingerprint",
                "k8s_pbs_password": "test-password",
            }
        if task.get("ansible.builtin.include_tasks", "").endswith(
            "fail-with-scrubbed-output.yml"
        ):
            task["ansible.builtin.include_tasks"] = str(SCRUB_FAILURE_TASK)
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"]]
            }
        if task.get("name") == "Authentik restore | Extract the selected PBS archive":
            task["ansible.builtin.command"]["argv"][0] = str(root / "bin/proxmox-backup-client")
        elif task.get("name", "").startswith("Authentik ") and "proxmox-backup-client" in str(
            task.get("ansible.builtin.command", {}).get("argv", [])
        ):
            task["ansible.builtin.command"]["argv"][0] = str(root / "bin/proxmox-backup-client")
        if task.get("name") == "Authentik restore | Require the installed PBS client":
            task["ansible.builtin.stat"]["path"] = str(root / "bin/proxmox-backup-client")
        if task.get("name") == "Authentik backup | Require the installed PBS client":
            task["ansible.builtin.stat"]["path"] = str(root / "bin/proxmox-backup-client")
        if task.get("name") in [
            "Authentik backup | Read the running Compose services",
            "Authentik restore | Read the running Compose services",
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
import sys
assert sys.argv[1:2] == ['compose']
if 'ps' in sys.argv:
    print('postgresql\nredis\nserver\nworker')
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
    if case!='backup-no-new-snapshot':
        (root/'new-snapshot-created').touch()
    raise SystemExit(0)
if args[:2]==['snapshot','list']:
    snapshots=[{'backup-time':1792112400}]
    if (root/'new-snapshot-created').exists():
        snapshots.append({'backup-time':1792116000})
    print(json.dumps(snapshots))
    raise SystemExit(0)
if args[:2]==['snapshot','files']:
    print(json.dumps(['data.pxar']))
    raise SystemExit(0)
if args[0]=='prune':
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
    install = task_named(main_tasks, "Install the PBS client for Authentik recovery")
    assert install["ansible.builtin.include_tasks"] == "install-pbs-client.yml"
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

    pbs = read_yaml(ROOT / "ansible/roles/authentik/tasks/install-pbs-client.yml")
    pbs_tasks = list(tasks_recursive(pbs))
    supported = task_named(pbs_tasks, "Authentik PBS client | Require a supported Debian release")
    assert supported["ansible.builtin.assert"]["that"] == [
        "ansible_distribution == 'Debian'",
        "ansible_distribution_release in ['bookworm', 'trixie']",
    ]


def check_backup_and_restore_contract():
    backup = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    backup_tasks = list(tasks_recursive(backup))
    assert "data.pxar" in str(backup)
    assert "proxmox-backup-client" in str(backup)
    assert "state: stopped" in yaml.safe_dump(backup)
    assert "state: present" in yaml.safe_dump(backup)
    assert "PG_VERSION" in str(backup)
    assert "_authentik_backup_expected_services" in str(backup)
    assert task_named(backup_tasks, "Authentik backup | Report a failed PBS upload")["ansible.builtin.include_tasks"].endswith(
        "fail-with-scrubbed-output.yml"
    )
    baseline = task_named(backup_tasks, "Authentik backup | Read the existing recovery points")
    assert baseline["ansible.builtin.command"]["argv"][-2:] == ["--output-format", "json"]
    verify_new_snapshot = task_named(
        backup_tasks, "Authentik backup | Confirm PBS recorded a new recovery point"
    )
    assert any("_authentik_backup_snapshots_before.stdout" in condition
               and "_authentik_backup_snapshots.stdout" in condition
               and "length" in condition
               for condition in verify_new_snapshot["ansible.builtin.assert"]["that"])

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
    assert "No Authentik data was changed" in str(restore)
    for task in restore_tasks:
        argv = task.get("ansible.builtin.command", {}).get("argv", [])
        if len(argv) > 2 and argv[:2] == ["proxmox-backup-client", "snapshot"] and "files" in argv:
            assert "--output-format" not in argv

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
    for case in ["success", "health-failure", "extract-failure"]:
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
                if case == "success":
                    assert result.returncode == 0, result.stdout + result.stderr
                    assert marker == "restored"
                    assert events == ["stopped", "present"]
                else:
                    assert result.returncode != 0, result.stdout + result.stderr
                    assert marker == "original"
                    expected_events = (
                        ["stopped", "present", "stopped", "present"]
                        if case == "health-failure"
                        else ["stopped", "stopped", "present"]
                    )
                    assert events == expected_events
                    assert "rollback" in result.stdout.lower() or "rollback" in result.stderr.lower()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


def check_backup_restarts_the_instance():
    backup_tasks = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    for case in ["success", "backup-failure", "backup-no-new-snapshot"]:
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
                    if case == "backup-no-new-snapshot":
                        assert "new pbs recovery point" in output, output
                    else:
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
