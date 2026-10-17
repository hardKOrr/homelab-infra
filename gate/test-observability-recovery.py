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
    """Run production file and command tasks; stub PBS resolution and Compose."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        if task.get("name") == "Observability backup | Push one application archive and retain the newest points":
            # Synthetic PBS values only; show the shim's assertion when a fixture regresses.
            task["no_log"] = False
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            restore_resolver = task.get("name", "").startswith("Observability restore")
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "test",
                "k8s_pbs_fingerprint": "test-fingerprint",
                "k8s_pbs_password": "test-password",
            }
            if restore_resolver:
                task["no_log"] = True
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
                "client": {"key_url": "test", "repository": "test", "suite": "test",
                           "component": "test", "image": "test"},
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
        "docker": r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
case=os.environ['OBSERVABILITY_TEST_CASE']
script=args[-1]
assert os.environ.get('PBS_REPOSITORY') == 'test'
assert os.environ.get('PBS_FINGERPRINT') == 'test-fingerprint'
assert os.environ.get('PBS_PASSWORD') == 'test-password'
if 'proxmox-backup-client backup' in script:
    volumes=[args[i+1] for i,value in enumerate(args[:-1]) if value=='--volume']
    root=Path(os.environ['OBSERVABILITY_TEST_ROOT'])
    assert volumes == [str(root/'data')+':/source:ro'], volumes
    assert 'proxmox-backup-client backup data.pxar:/source --backup-id' in script
    assert 'proxmox-backup-client prune' in script
    raise SystemExit(7 if case=='backup-failure' else 0)
if 'proxmox-backup-client snapshot list' in script:
    if case != 'missing-source-point':
        print('backup-time 2026-10-15T22:00:00Z')
    if case == 'missing-source-point':
        print('backup-time 2026-10-16T01:00:00Z')
    else:
        print('backup-time 2026-10-16T01:00:00Z')
    raise SystemExit(0)
if 'proxmox-backup-client restore' in script:
    assert os.environ['SNAPSHOT'] == 'host/observability/2026-10-15T22:00:00Z'
    if case == 'extract-failure':
        raise SystemExit(7)
    mount=args[args.index('--volume')+1]
    assert mount.endswith(':/restore'), mount
    stage=Path(mount[:-9])
    extracted=stage/'data'
    (extracted/'grafana').mkdir(parents=True)
    (extracted/'grafana/grafana.db').write_text('restored grafana database\n')
    if case != 'incomplete-archive':
        (extracted/'prometheus').mkdir()
        (extracted/'prometheus/marker').write_text('restored prometheus data\n')
    raise SystemExit(0)
raise AssertionError(script)
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
        states = (root / "services").read_text().splitlines()
        assert states == ["stopped", "present"], states


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
                    "missing-source-point"]:
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
    backup_task = next(task for task in all_tasks(backup_source) if task.get("name") ==
                       "Observability backup | Push one application archive and retain the newest points")
    assert backup_task.get("no_log") is True
    restored_path = next(task for task in all_tasks(restore_source) if task.get("name") ==
                         "Observability restore | Recreate the data directory")
    assert restored_path["ansible.builtin.file"]["owner"] == "root"
    assert restored_path["ansible.builtin.file"]["group"] == "root"
    assert restored_path["ansible.builtin.file"]["mode"] == "0755"
    extract_task = next(task for task in all_tasks(restore_source) if task.get("name") ==
                        "Observability restore | Extract the selected archive into staging")
    extract_command = extract_task["ansible.builtin.command"]
    extract_script = extract_command["argv"][-1]
    assert extract_command["argv"][extract_command["argv"].index("--volume") + 1].endswith(":/restore")
    for line in [
        "install -d -m 0700 /restore/tmp /restore/home/.cache /restore/home/.config",
        "export TMPDIR=/restore/tmp",
        "export HOME=/restore/home",
        "export XDG_CACHE_HOME=/restore/home/.cache",
        "export XDG_CONFIG_HOME=/restore/home/.config",
        'proxmox-backup-client restore "$SNAPSHOT" data.pxar /restore/data',
    ]:
        assert line in extract_script, line
    assert '{{ _observability_restore_tmp.path }}/data/.' in next(
        task for task in all_tasks(restore_source)
        if task.get("name") == "Observability restore | Copy the archived application state into place"
    )["ansible.builtin.command"]["argv"]
    for task in all_tasks(restore_source):
        if task.get("ansible.builtin.command", {}).get("argv", [None])[0] == "docker":
            assert task.get("no_log") is True, task.get("name")


def main():
    check_contract()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["backup-success", "backup-failure"]:
            backup_check(case, server)
        for case in ["plan", "selected-plan", "foreign-group", "missing-pre-restore-point",
                     "missing-source-point", "extract-failure", "incomplete-archive",
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
