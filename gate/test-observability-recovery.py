#!/usr/bin/env python3
"""Exercise Observability backup, preview, restore and rollback with local shims."""

import copy
import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from pbs_client_stub import stub_pbs_client

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/observability/tasks/backup.yml").read_text())
restore_source = yaml.safe_load((repo / "ansible/roles/observability/tasks/restore.yml").read_text())
source_snapshot = "host/observability/2026-10-15T22:00:00Z"
pre_restore_point = "host/observability/2026-10-16T01:00:00Z"


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


def restore_check(case, server):
    with tempfile.TemporaryDirectory(prefix="observability-restore-test-") as directory:
        root = Path(directory)
        make_stubs(root)
        data = make_data(root)
        server.response_status = 503 if case == "health-failure" else 200
        selected = ("host/foreign/2026-10-15T22:00:00Z" if case == "foreign-group"
                    else source_snapshot)
        variables = base_variables(root, server) | {
            "restore_snapshot": selected,
            "restore_pre_restore_point": pre_restore_point,
            "restore_overwrite": case not in ["plan", "selected-plan"],
            "restore_destination": "existing",
        }
        if case == "missing-pre-restore-point":
            variables["restore_pre_restore_point"] = ""
        if case == "plan":
            variables["restore_snapshot"] = ""
        result = run_tasks(root, restore_source, variables, case)
        success = case == "plan" or case == "selected-plan" or case == "success"
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert data.exists()
        if case == "success":
            assert (data / "grafana/grafana.db").read_text() == "restored grafana database\n"
            assert (data / "prometheus/marker").read_text() == "restored prometheus data\n"
        else:
            assert (data / "grafana/grafana.db").read_text() == "original grafana database\n"
            assert (data / "prometheus/marker").read_text() == "original prometheus data\n"
        assert not list(root.glob(".observability-pre-restore-*")), result.stdout + result.stderr
        assert not list(root.glob("observability-observability-restore-*"))
        if case in ["plan", "selected-plan", "foreign-group", "missing-pre-restore-point",
                    "extract-failure", "incomplete-archive"]:
            assert not (root / "services").exists(), "plan or rejected restore stopped services"
        else:
            assert (root / "services").exists(), f"{case}: restore never reached its service boundary\n{result.stdout}{result.stderr}"
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped"), states
        if case == "foreign-group":
            assert "must belong to host/observability/" in result.stdout
        if case == "missing-pre-restore-point":
            assert "requires a separate pre_restore_point" in result.stdout


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
    staging = next(task for task in all_tasks(restore_source) if task.get("name") ==
                   "Observability restore | Create a private staging directory")
    assert staging["ansible.builtin.tempfile"]["path"] == "{{ app_config.app.data_path | dirname }}"
    assert "client" not in defaults["backup"], "the shared client owns its pins"
    upload = next(task for task in all_tasks(backup_source)
                  if task.get("ansible.builtin.include_tasks", "").endswith("pbs-client-backup.yml"))
    assert upload["vars"]["pbs_client_stop_services"] == ["prometheus", "grafana"]
    restored_path = next(task for task in all_tasks(restore_source) if task.get("name") ==
                         "Observability restore | Recreate the data directory")
    assert restored_path["ansible.builtin.file"]["owner"] == "root"
    assert restored_path["ansible.builtin.file"]["group"] == "root"
    assert restored_path["ansible.builtin.file"]["mode"] == "0755"
    names = [task.get("name") for task in all_tasks(restore_source)]
    assert names.index("Observability restore | Refuse an incomplete archive") < names.index(
        "Observability restore | Stop only Prometheus and Grafana"), "check the point before stopping"
    assert '{{ _observability_restore_tmp.path }}/data/.' in next(
        task for task in all_tasks(restore_source)
        if task.get("name") == "Observability restore | Copy the archived application state into place"
    )["ansible.builtin.command"]["argv"]


def main():
    check_contract()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["backup-success", "backup-failure"]:
            backup_check(case, server)
        for case in ["plan", "selected-plan", "foreign-group", "missing-pre-restore-point",
                     "extract-failure", "incomplete-archive",
                     "move-failure", "copy-failure", "start-failure", "health-failure",
                     "success"]:
            restore_check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Observability: native backup, read-only plan, exact group guards, restore and post-move rollback passed")


if __name__ == "__main__":
    main()
