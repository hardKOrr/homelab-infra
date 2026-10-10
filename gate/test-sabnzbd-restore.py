#!/usr/bin/env python3
"""Run SABnzbd's restore file operations and rollback against temporary local data."""

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
source = yaml.safe_load((repo / "ansible/roles/sabnzbd/tasks/restore.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api?mode=queue&output=json&apikey=fixture-key"
        self.send_response(self.server.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"queue": {}}' if self.server.response_status == 200 and not self.server.reject_key else b'{"error": "API Key Incorrect"}')

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root, case):
    """Stub PBS resolution, Compose and Vault publication; run the production file/command tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True, "k8s_pbs_repository": "test",
                "k8s_pbs_fingerprint": "test", "k8s_pbs_password": "test",
            }
        if task.get("ansible.builtin.include_tasks", "").endswith("upsert-item.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.assert"] = {"that": ["_sabnzbd_api_key == 'fixture-key'"]}
            if case == "credential-failure":
                task["ansible.builtin.assert"] = {"that": [False]}
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
                task[key] = fixture_tasks(task[key], root, case)
    return tasks


def check(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        config.mkdir()
        (config / "original.txt").write_text("original state\n")
        before_mode = config.stat().st_mode
        # The executable stubs are the process boundary for the unmodified production
        # Docker and cp argv. Copy failure writes partial state before failing.
        scripts = {
            "docker": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert os.environ.get('PBS_REPOSITORY') == 'test'
assert 'host/sabnzbd/2026-10-09T00:00:00Z' == os.environ['SNAPSHOT']
if os.environ['SABNZBD_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
args=sys.argv[1:]
mount=args[args.index('--volume')+1]
assert mount.endswith(':/restore')
Path(mount[:-9], 'restored.txt').write_text('restored state\\n')
text='[misc]\\n' if os.environ['SABNZBD_TEST_CASE']=='missing-auth' else '[misc]\\napi_key = fixture-key\\n'
Path(mount[:-9], 'sabnzbd.ini').write_text(text)
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['SABNZBD_TEST_CASE']=='copy-failure' else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['SABNZBD_TEST_CASE']=='move-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "chown": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['SABNZBD_TEST_CASE']=='ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown',*sys.argv[1:]]).returncode)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['sabnzbd']
with Path(os.environ['SABNZBD_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['SABNZBD_TEST_CASE']=='start-failure':
    raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = ("host/sabnzbd-other/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/sabnzbd/2026-10-09T00:00:00Z")
        server.response_status = 503 if case == "health-failure" else 200
        server.reject_key = case == "invalid-key"
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "sabnzbd", "sabnzbd_project_dir": str(root),
            "restore_snapshot": snapshot, "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "app_config": {"app": {"config_path": str(config), "puid": os.getuid(),
                                   "pgid": os.getgid(), "port": server.server_port},
                           "backup": {"enabled": True, "application_consistent": True,
                                      "client": {"key_url": "test", "repository": "test",
                                                 "suite": "test", "component": "test",
                                                 "image": "test"}}},
        }, "tasks": fixture_tasks(source, root, case)}]
        path = root / "restore.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "SABNZBD_TEST_CASE": case, "SABNZBD_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["success", "plan"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert config.exists()
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "restored.txt").read_text() == "restored state\n"
        else:
            assert (config / "original.txt").read_text() == "original state\n"
            assert not (config / "restored.txt").exists()
            assert config.stat().st_mode == before_mode
        assert not list(root.glob(".sabnzbd-pre-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")
        if case == "foreign-group":
            assert "must belong to host/sabnzbd/" in result.stdout


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "extract-failure", "move-failure", "copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "credential-failure", "missing-auth", "invalid-key", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("SABnzbd: read-only plan, group guard, successful restore and failure rollback passed")


if __name__ == "__main__":
    main()
