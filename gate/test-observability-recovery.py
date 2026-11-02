#!/usr/bin/env python3
"""Exercise Observability backup with local shims; check its restore-swap inputs and hooks.

gate/test-restore-swap.py proves the swap's preview, staging, ownership and rollback.

Checks: both-service quiescence, the complete data archive, and the restore swap inputs:
Prometheus and Grafana trees with their own uids, the staged Grafana database hook and the
Grafana health hook.
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

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/observability/tasks/backup.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(self.server.response_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Run production file and command tasks; stub the PBS client and Compose."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"], *compose["services"]]
            }
            task["changed_when"] = True
        if task.get("name") == "Observability restore | Recreate the data directory":
            # The real restore play runs as root; keep the local fixture unprivileged.
            task["ansible.builtin.file"].pop("owner")
            task["ansible.builtin.file"].pop("group")
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
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "ANSIBLE_NOCOLOR": "1",
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "OBSERVABILITY_TEST_CASE": case,
        "OBSERVABILITY_TEST_ROOT": str(root),
    }


def run_tasks(root, tasks, variables, case):
    playbook = root / "run.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": fixture_tasks(tasks, root),
    }]))
    return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                          env=environment(root, case), text=True, capture_output=True,
                          check=False)


def base_variables(root, server):
    return {
        "instance": "observability",
        "observability_project_dir": str(root),
        "homelabinfra_infra": {"backups": {}},
        "app_config": {
            "app": {"data_path": str(root / "data"), "port": server.server_port,
                    "prometheus_port": server.server_port},
            "backup": {
                "enabled": True, "application_consistent": True,
                "retention_days": 14, "datastore": "", "backup_id": "",
            },
        },
    }


def make_data(root, value="original"):
    data = root / "data"
    (data / "grafana").mkdir(parents=True)
    (data / "grafana/grafana.db").write_text(value + " grafana database\n")
    (data / "prometheus").mkdir()
    (data / "prometheus/marker").write_text(value + " prometheus data\n")
    return data


def make_stubs(root):
    bin_dir = root / "bin"
    bin_dir.mkdir()
    scripts = {
        "proxmox-backup-client": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['OBSERVABILITY_TEST_CASE']
root=Path(os.environ['OBSERVABILITY_TEST_ROOT'])
if args[0]=='backup':
    assert args==['backup', f'data.pxar:{root}/data'], args
    raise SystemExit(7 if case=='backup-failure' else 0)
if args[:2]==['snapshot','list']:
    print('host/observability/2026-10-15T22:00:00Z')
    raise SystemExit(0)
assert args[:3]==['restore','host/observability/2026-10-15T22:00:00Z','data.pxar'], args
if case=='extract-failure':
    raise SystemExit(7)
extracted=Path(args[3])
assert extracted.is_dir() and extracted.name=='data', extracted
(extracted/'grafana').mkdir()
(extracted/'grafana/grafana.db').write_text('restored grafana database\n')
if case!='incomplete-archive':
    (extracted/'prometheus').mkdir()
    (extracted/'prometheus/marker').write_text('restored prometheus data\n')
''',
        "service": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sorted(sys.argv[2:]) == ['grafana','prometheus'], sys.argv
root=Path(os.environ['OBSERVABILITY_TEST_ROOT'])
with (root/'services').open('a') as log:
    log.write(sys.argv[1]+'\n')
if sys.argv[1]=='present' and os.environ['OBSERVABILITY_TEST_CASE']=='start-failure':
    raise SystemExit(7)
''',
        "cp": r'''#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['OBSERVABILITY_TEST_CASE']=='copy-failure' else result.returncode)
''',
        "mv": r'''#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['OBSERVABILITY_TEST_CASE']=='move-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
''',
    }
    for name, script in scripts.items():
        path = bin_dir / name
        path.write_text(script)
        path.chmod(0o700)


def backup_check(case, server):
    with tempfile.TemporaryDirectory(prefix="observability-backup-test-") as directory:
        root = Path(directory)
        make_stubs(root)
        make_data(root)
        result = run_tasks(root, backup_source, base_variables(root, server), case)
        success = case == "backup-success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert (root / "data/grafana/grafana.db").read_text() == "original grafana database\n"


def check_restore_swap(server):
    swap = swap_inputs("observability")
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    # Each tree keeps the uid its container runs as; data_path itself stays as Deploy made it.
    assert swap["recovery_swap_paths"] == [
        {"path": "{{ app_config.app.data_path }}/prometheus", "source": "data/prometheus",
         "owner": 65534, "group": 0, "mode": "0750"},
        {"path": "{{ app_config.app.data_path }}/grafana", "source": "data/grafana",
         "owner": 472, "group": 0, "mode": "0750"}]
    assert swap["recovery_swap_staging_parent"] == "{{ app_config.app.data_path | dirname }}"
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _observability_restore_target_backup_id }}"
    assert swap["recovery_swap_stop_services"] == ["prometheus", "grafana"]
    assert swap["recovery_swap_health_url"].endswith("{{ app_config.app.prometheus_port }}/-/ready")
    tasks = yaml.safe_load((repo / "ansible/roles/observability/tasks/restore.yml").read_text())
    assert "Observability restore | Require an independent pre-restore point for an existing target" in [
        task.get("name") for task in tasks]

    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        database = stage / "data/grafana/grafana.db"
        database.parent.mkdir(parents=True)
        variables = {"recovery_swap_stage": str(stage)}
        assert run_hook("observability", "staged", variables).returncode != 0, "no grafana.db"
        database.write_text("")
        assert run_hook("observability", "staged", variables).returncode != 0, "empty grafana.db"
        database.write_text("restored grafana database\n")
        result = run_hook("observability", "staged", variables)
        assert result.returncode == 0, result.stdout + result.stderr

    def stub(tasks):
        for task in tasks:
            task.update(retries=1, delay=0)
        return tasks

    for status, ok in [(503, False), (200, True)]:
        server.response_status = status
        result = run_hook("observability", "verified",
                          {"app_config": {"app": {"port": server.server_port}}}, stub)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr


def check_contract():
    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/observability.yml").read_text())[
        "observability_defaults"]
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())[
        "applications"]["observability"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"] == {
        "backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"}
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert {"backup", "restore"} <= set(catalog["extra"])
    assert "client" not in defaults["backup"], "the shared client owns its pins"
    upload = next(task for task in all_tasks(backup_source)
                  if task.get("ansible.builtin.include_tasks", "").endswith("pbs-client-backup.yml"))
    assert upload["vars"]["pbs_client_stop_services"] == ["prometheus", "grafana"]


def main():
    check_contract()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["backup-success", "backup-failure"]:
            backup_check(case, server)
        check_restore_swap(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Observability: native backup, restore swap inputs and hooks passed")


if __name__ == "__main__":
    main()
