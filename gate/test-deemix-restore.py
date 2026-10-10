#!/usr/bin/env python3
"""Run Deemix's restore file operations and rollback against temporary local data."""

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
source = yaml.safe_load((repo / "ansible/roles/deemix/tasks/restore.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(self.server.response_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub only PBS resolution and Compose; run the production file/command tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
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
assert 'host/deemix/2026-10-09T00:00:00Z' == os.environ['SNAPSHOT']
if os.environ['DEEMIX_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
args=sys.argv[1:]
mount=args[args.index('--volume')+1]
assert mount.endswith(':/restore')
Path(mount[:-9], 'restored.txt').write_text('restored state\\n')
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['DEEMIX_TEST_CASE'] in ['copy-failure', 'rollback-failure'] else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['DEEMIX_TEST_CASE']=='move-failure' or (os.environ['DEEMIX_TEST_CASE']=='rollback-failure' and sys.argv[1].endswith('/config') and '.deemix-pre-restore-' in sys.argv[1]):
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "chown": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['DEEMIX_TEST_CASE']=='ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown',*sys.argv[1:]]).returncode)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['deemix']
with Path(os.environ['DEEMIX_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['DEEMIX_TEST_CASE']=='start-failure':
    raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = ("host/deemix-other/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/deemix/2026-10-09T00:00:00Z")
        server.response_status = 503 if case == "health-failure" else 200
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "deemix", "deemix_project_dir": str(root),
            "restore_snapshot": snapshot, "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "app_config": {"app": {"config_path": str(config), "puid": os.getuid(),
                                   "pgid": os.getgid(), "port": server.server_port},
                           "backup": {"enabled": True, "application_consistent": True,
                                      "client": {"key_url": "test", "repository": "test",
                                                 "suite": "test", "component": "test",
                                                 "image": "test"}}},
        }, "tasks": fixture_tasks(source, root)}]
        path = root / "restore.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "DEEMIX_TEST_CASE": case, "DEEMIX_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["success", "plan"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert config.exists() or case == "rollback-failure"
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "restored.txt").read_text() == "restored state\n"
        elif case == "rollback-failure":
            retained = list(root.glob(".deemix-pre-restore-*/config/original.txt"))
            assert len(retained) == 1
            assert retained[0].read_text() == "original state\n"
            assert (config / "restored.txt").exists() is False
        else:
            assert (config / "original.txt").read_text() == "original state\n"
            assert not (config / "restored.txt").exists()
            assert config.stat().st_mode == before_mode
        if case != "rollback-failure":
            assert not list(root.glob(".deemix-pre-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")
        if case == "foreign-group":
            assert "must belong to host/deemix/" in result.stdout


def check_backup(fail_upload):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        config.mkdir()
        (config / "settings.json").write_text('{"tracknameTemplate":"canary"}')
        scripts = {
            "docker": """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
assert args.count('--volume') == 1
assert args[args.index('--volume')+1] == os.environ['DEEMIX_TEST_ROOT']+'/config:/source:ro'
assert os.environ['BACKUP_ID'] == 'deemix'
assert 'backup data.pxar:/source --backup-id "$BACKUP_ID"' in args[-1]
assert '--keep-last 2' in args[-1]
raise SystemExit(7 if os.environ['FAIL_UPLOAD'] == '1' else 0)
""",
            "service": """#!/usr/bin/env python3
import os, sys
from pathlib import Path
assert sys.argv[2:] == ['deemix']
with Path(os.environ['DEEMIX_TEST_ROOT'], 'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/deemix.yml").read_text())["deemix_defaults"]
        defaults["app"]["config_path"] = str(config)
        tasks = yaml.safe_load((repo / "ansible/roles/deemix/tasks/backup.yml").read_text())
        path = root / "backup.yml"
        path.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "deemix", "deemix_project_dir": str(root),
                     "app_config": defaults, "homelabinfra_infra": {"backups": {}}},
            "tasks": fixture_tasks(tasks, root),
        }]))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
                            "DEEMIX_TEST_ROOT": str(root), "FAIL_UPLOAD": str(int(fail_upload))}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert (result.returncode == 0) != fail_upload, result.stdout + result.stderr
        assert (root / "services").read_text().splitlines() == ["stopped", "present"]
        assert (config / "settings.json").read_text() == '{"tracknameTemplate":"canary"}'


def check_empty_arl():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = root / "settings.json"
        settings.write_text('{"tracknameTemplate":"canary"}')
        deploy = yaml.safe_load((repo / "ansible/roles/deemix/tasks/main.yml").read_text())
        tasks = [copy.deepcopy(next(t for t in deploy if t["name"] == name))
                 for name in ["Resolve the app kind and ARL", "Seed the Deezer ARL"]]
        path = root / "empty.yml"
        path.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"app_config": {"app": {"config_path": str(root), "media_kind": "deemix",
                                           "port": 6595, "puid": os.getuid(), "pgid": os.getgid()}}},
            "tasks": tasks,
            "handlers": [{"name": "Restart deemix", "ansible.builtin.debug": {"msg": "restart"}}],
        }]))
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / ".arl").read_text() == ""
        assert settings.read_text() == '{"tracknameTemplate":"canary"}'


def main():
    check_empty_arl()
    check_backup(False)
    check_backup(True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "extract-failure", "move-failure", "copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "rollback-failure", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Deemix: read-only plan, group guard, successful restore and failure rollback passed")


if __name__ == "__main__":
    main()
