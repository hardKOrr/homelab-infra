#!/usr/bin/env python3
"""Run slskd's production restore file operations and rollback locally."""

import copy
import http.server
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
source = yaml.safe_load((repo / "ansible/roles/slskd/tasks/restore.yml").read_text())
backup = yaml.safe_load((repo / "ansible/roles/slskd/tasks/backup.yml").read_text())


class Api(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        valid = self.headers.get("X-Api-Key") == "fixture-api-key"
        status = self.server.response_status if valid else 401
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if status == 200:
            self.wfile.write(json.dumps({
                "version": {"current": "0.23.0"},
                "server": {"isLoggedIn": True},
            }).encode())

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub PBS resolution and Compose; run production file and command tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True, "k8s_pbs_repository": "test",
                "k8s_pbs_fingerprint": "test", "k8s_pbs_password": "test",
            }
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
        if case != "missing-target":
            config.mkdir()
            (config / "original.txt").write_text("original state\n")
        before_mode = config.stat().st_mode if config.exists() else None
        # These stubs fail at the process boundary while preserving the production argv.
        # Copy failure writes partial data before returning an error.
        scripts = {
            "docker": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert os.environ.get('PBS_REPOSITORY') == 'test'
assert os.environ.get('SNAPSHOT') == 'host/slskd/2026-10-09T00:00:00Z'
if os.environ['SLSKD_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
args=sys.argv[1:]
mount=args[args.index('--volume')+1]
assert mount.endswith(':/restore')
Path(mount[:-9], 'restored.txt').write_text('restored state\\n')
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['SLSKD_TEST_CASE']=='copy-failure' else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['SLSKD_TEST_CASE']=='move-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "chown": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['SLSKD_TEST_CASE']=='ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown',*sys.argv[1:]]).returncode)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['slskd']
with Path(os.environ['SLSKD_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['SLSKD_TEST_CASE']=='start-failure':
    raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = ("host/slskd-other/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/slskd/2026-10-09T00:00:00Z")
        server.response_status = 503 if case == "health-failure" else 200
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "slskd", "slskd_project_dir": str(root),
            "slskd_webui_retries": 1, "slskd_webui_delay": 0,
            "restore_snapshot": snapshot,
            "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "homelabinfra_vault": {"media": {"slskd": {"api_key": "fixture-api-key"}}},
            "app_config": {"app": {"config_path": str(config), "puid": os.getuid(),
                                   "pgid": os.getgid(), "port": server.server_port},
                           "backup": {"enabled": True, "application_consistent": True,
                                      "client": {"key_url": "test", "repository": "test",
                                                 "suite": "test", "component": "test",
                                                 "image": "test"}}},
        }, "tasks": fixture_tasks(source, root)}]
        if case == "missing-point":
            plays[0]["vars"]["restore_snapshot"] = ""
        if case == "api-key-failure":
            plays[0]["vars"]["homelabinfra_vault"]["media"]["slskd"]["api_key"] = "wrong-fixture-key"
        path = root / "restore.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "SLSKD_TEST_CASE": case, "SLSKD_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["success", "plan"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        if case == "missing-target":
            assert not config.exists()
            assert not (root / "services").exists()
            assert "data path" in result.stdout.lower()
            return
        assert config.exists()
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "restored.txt").read_text() == "restored state\n"
        else:
            assert (config / "original.txt").read_text() == "original state\n"
            assert not (config / "restored.txt").exists()
            assert config.stat().st_mode == before_mode
        assert not list(root.glob(".slskd-pre-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group", "missing-point"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")
        if case == "foreign-group":
            assert "must belong to host/slskd/" in result.stdout
        if case == "missing-point":
            assert "slskd restore needs" in result.stdout.lower()


def check_backup_contract():
    stop = next(task for task in backup if task["name"] == "slskd backup | Stop only slskd")
    assert stop["community.docker.docker_compose_v2"]["state"] == "stopped"
    assert stop["community.docker.docker_compose_v2"]["services"] == ["{{ instance }}"]
    upload = next(task for task in backup if task["name"] ==
                  "slskd backup | Archive the quiesced data directory to PBS")
    push = next(task for task in upload["block"] if task["name"] ==
                "slskd backup | Push one application archive")
    argv = push["ansible.builtin.command"]["argv"]
    script = argv[-1]
    assert argv.count("{{ app_config.app.config_path }}:/source:ro") == 1
    assert script.count("data.pxar:") == 1
    assert "data.pxar:/source" in script
    assert "--exclude /logs" in script
    assert "--keep-last 2 --keep-daily" in script
    assert "download_path" not in script and "/downloads" not in script
    assert upload["always"][0]["community.docker.docker_compose_v2"]["state"] == "present"


def main():
    check_backup_contract()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "missing-point", "missing-target", "extract-failure",
                     "move-failure", "copy-failure", "ownership-failure", "start-failure",
                     "health-failure", "api-key-failure", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("slskd: single-config backup contract, read-only plan, group guard, authenticated restore and rollback passed")


if __name__ == "__main__":
    main()
