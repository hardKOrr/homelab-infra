#!/usr/bin/env python3
"""Exercise Authentik's application-consistent PBS backup and check its restore-swap inputs.

gate/test-restore-swap.py proves the swap itself, including `live` owners and the native
PBS client under Compose services.
"""

import copy
import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from pbs_client_stub import stub_pbs_client
from restore_swap_inputs import run_hook, swap_inputs


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
        status = 204
        self.send_response(status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"]]
            }
        if task.get("name") == "Authentik backup | Read the running Compose services":
            task["ansible.builtin.command"]["argv"][0] = str(root / "bin/docker")
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
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
        server.calls = 0
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": variables,
        "tasks": fixture_tasks(tasks, root),
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


def check_directory_password_continuity():
    main = read_yaml(ROOT / "ansible/roles/authentik/tasks/main.yml")
    store = task_named(main, "Store Authentik secrets before applying them")
    shared = read_yaml(ROOT / "ansible/tasks/bitwarden/upsert-item.yml")
    comparison = [task_named(shared, name) for name in [
        "Vault | Resolve the stored and effective field sets",
        "Vault | Compare the stored item with the effective one",
    ]]
    values = {
        "provider": "authentik", "instance": "authentik",
        "host": "http://127.0.0.1:9000", "token": "api-token",
        "admin_user": "akadmin", "admin_password": "admin-password",
        "postgres_password": "postgres-password", "secret_key": "secret-key",
        "user_alice_password": "directory-password",
    }
    hidden = {"token", "admin_password", "postgres_password", "secret_key",
              "user_alice_password"}
    variables = {
        **store["vars"],
        "_authentik_vault_item": "homelab-infra/sso",
        "instance": "authentik", "ansible_host": "127.0.0.1",
        "app_config": {"app": {"port": 9000}},
        "authentik_admin_user": "akadmin", "_authentik_api_token": "api-token",
        "_authentik_admin_password": "admin-password",
        "_authentik_pg_pass": "postgres-password", "_authentik_secret_key": "secret-key",
        "_vault_collection_id": "collection",
        "_vault_existing_items": [{
            "name": "homelab-infra/sso", "collectionIds": ["collection"],
            "fields": [{"name": k, "value": v, "type": int(k in hidden)}
                       for k, v in values.items()],
        }],
    }
    with tempfile.TemporaryDirectory(prefix="authentik-directory-") as temporary:
        root = Path(temporary)
        for password, matches in [("admin-password", True), ("rotated-password", False)]:
            variables["_authentik_admin_password"] = password
            tasks = comparison + [{"ansible.builtin.assert": {"that": [
                f"_vault_item_matches == {matches}",
                "_vault_effective_values.user_alice_password == 'directory-password'",
                "'user_alice_password' in _vault_effective_hidden",
                f"_vault_effective_values.admin_password == '{password}'",
            ]}}]
            result = run_tasks(root, tasks, variables, "directory-passwords")
            assert result.returncode == 0, result.stdout + result.stderr


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

    swap = swap_inputs("authentik")
    assert swap["recovery_swap_runtime"] == "docker" and swap["recovery_swap_pbs_runtime"] == "native"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_backup_id"] == "{{ _authentik_restore_source_backup_id }}"
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _authentik_restore_target_backup_id }}"
    assert swap["recovery_swap_staging_parent"] == "{{ _authentik_restore_project_dir }}"
    paths = {p["source"]: p for p in swap["recovery_swap_paths"]}
    assert sorted(paths) == ["data/" + name for name in
                             ["certs", "custom-templates", "database", "media", "redis"]]
    for source, path in paths.items():
        assert path["path"] == "{{ _authentik_restore_data_path }}/" + source.removeprefix("data/")
    # PostgreSQL and Redis keep the owner their own container gave the live directory.
    for source in ["data/database", "data/redis"]:
        assert (paths[source]["owner"], paths[source]["group"]) == ("live", "live")
    assert paths["data/database"]["mode"] == "0700" and not paths["data/database"].get("allow_empty")
    for source in ["data/media", "data/certs", "data/custom-templates"]:
        assert (paths[source]["owner"], paths[source]["group"]) == (1000, 1000), source
    assert swap["recovery_swap_stop_services"] == _authentik_services()
    assert swap["recovery_swap_health_url"].endswith("/-/health/ready/")
    restore = read_yaml(ROOT / "ansible/roles/authentik/tasks/restore.yml")
    assert task_named(restore, "Authentik restore | Validate the independent pre-restore group")["when"] \
        == "restore_overwrite | bool"

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


def _authentik_services():
    backup = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    return task_named(backup, "Authentik backup | Resolve the instance paths and recovery identity")[
        "ansible.builtin.set_fact"]["_authentik_backup_expected_services"]


def check_staged_hook():
    with tempfile.TemporaryDirectory(prefix="authentik-staged-") as tmp:
        stage = Path(tmp)
        make_instance_tree(stage / "data", "restored")
        variables = {"recovery_swap_stage": str(stage)}
        result = run_hook("authentik", "staged", variables)
        assert result.returncode == 0, result.stdout + result.stderr
        (stage / "data/database/PG_VERSION").unlink()
        assert run_hook("authentik", "staged", variables).returncode != 0, "a point without PostgreSQL"


def check_backup_restarts_the_instance():
    backup_tasks = read_yaml(ROOT / "ansible/roles/authentik/tasks/backup.yml")
    for case in ["success", "backup-failure"]:
        with tempfile.TemporaryDirectory(prefix="authentik-backup-") as tmp:
            root = Path(tmp)
            write_fixture_scripts(root)
            server = http.server.HTTPServer(("127.0.0.1", 0), Health)
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
    check_directory_password_continuity()
    check_backup_and_restore_contract()
    check_backup_restarts_the_instance()
    check_staged_hook()
    print("Authentik native recovery: contract, estate-scoped target, secret continuity, swap inputs and staged check passed.")


if __name__ == "__main__":
    main()
