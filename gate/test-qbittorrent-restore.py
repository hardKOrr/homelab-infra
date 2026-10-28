#!/usr/bin/env python3
"""Run qBittorrent's restore file operations and rollback against temporary local data."""

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
source = yaml.safe_load((repo / "ansible/roles/qbittorrent/tasks/restore.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(self.server.response_status)
        self.end_headers()

    def do_POST(self):
        assert self.path == "/api/v2/auth/login"
        body = self.rfile.read(int(self.headers["Content-Length"]))
        assert b"username=admin" in body and b"password=fixture-password" in body
        self.send_response(401 if self.server.reject_login else 204)
        if not self.server.reject_login:
            self.send_header("Set-Cookie", "SID=fixture; HttpOnly")
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub only the PBS client and Compose; run the production file/command tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if task.get("ansible.builtin.include_tasks") == "login.yml":
            task["ansible.builtin.include_tasks"] = str(repo / "ansible/roles/qbittorrent/tasks/login.yml")
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
                task[key] = fixture_tasks(task[key], root)
    return tasks


def check(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        config.mkdir()
        (config / "original.txt").write_text("original state\n")
        before_mode = config.stat().st_mode
        # The executable stubs are the process boundary for the PBS client and the
        # unmodified production cp argv. Copy failure writes partial state before failing.
        scripts = {
            "proxmox-backup-client": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
if args[:2] == ['snapshot', 'list']:
    raise SystemExit(0)
assert args[:2] == ['restore', 'host/qbittorrent/2026-10-09T00:00:00Z'], args
if os.environ['QBITTORRENT_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
assert args[2] == 'data.pxar'
staging=Path(args[3])
Path(staging, 'restored.txt').write_text('restored state\\n')
if os.environ['QBITTORRENT_TEST_CASE'] != 'missing-state':
    Path(staging, 'qBittorrent/BT_backup').mkdir(parents=True)
    Path(staging, 'qBittorrent/qBittorrent.conf').write_text('[Preferences]\\n')
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['QBITTORRENT_TEST_CASE']=='copy-failure' else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['QBITTORRENT_TEST_CASE']=='move-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "chown": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['QBITTORRENT_TEST_CASE']=='ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown',*sys.argv[1:]]).returncode)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['qbittorrent']
with Path(os.environ['QBITTORRENT_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['QBITTORRENT_TEST_CASE']=='start-failure':
    raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = "host/qbittorrent/2026-10-09T00:00:00Z"
        server.response_status = 503 if case == "health-failure" else 200
        server.reject_login = case == "credential-failure"
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "qbittorrent", "qbittorrent_project_dir": str(root),
            "restore_snapshot": snapshot, "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "homelabinfra_vault": {"media": {"qbittorrent": {"username": "admin", "password": "fixture-password"}}},
            "app_config": {"app": {"config_path": str(config), "puid": os.getuid(),
                                   "pgid": os.getgid(), "port": server.server_port},
                           "backup": {"enabled": True, "application_consistent": True}},
        }, "tasks": fixture_tasks(source, root)}]
        path = root / "restore.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "QBITTORRENT_TEST_CASE": case, "QBITTORRENT_TEST_ROOT": str(root),
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
        assert not list(root.glob(".qbittorrent-pre-restore-*")), result.stdout + result.stderr
        if case == "plan":
            assert not (root / "services").exists(), "read-only restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "extract-failure", "move-failure", "copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "credential-failure", "missing-state", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("qBittorrent: read-only plan, successful restore and failure rollback passed")


if __name__ == "__main__":
    main()
