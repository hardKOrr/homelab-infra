#!/usr/bin/env python3
"""Run Plex's restore file operations and rollback against temporary local data."""

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
source = yaml.safe_load((repo / "ansible/roles/plex/tasks/restore.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/library/sections":
            valid = self.headers.get("X-Plex-Token") == "restored token"
            self.send_response(200 if valid and not self.server.token_failure else 401)
            self.end_headers()
            return
        self.send_response(self.server.response_status)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub PBS, Compose and Vault publication; run production state and HTTP tasks."""
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
            task["ansible.builtin.command"] = {"argv": [str(root / "bin/publish"),
                                                       "{{ _plex_restored_token }}"]}
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
        config = root / "config" / "Plex Media Server"
        config.mkdir(parents=True)
        (config / "original.txt").write_text("original state\n")
        for name in ["Preferences.xml", "Plug-in Support/Databases/library.db",
                     "Metadata/item/info", "Media/item/info"]:
            path = config / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original " + name)
        original = {str(p.relative_to(config)): (p.read_bytes(), p.stat().st_ino,
                                               p.stat().st_mode, p.stat().st_uid, p.stat().st_gid)
                    for p in config.rglob("*") if p.is_file()}
        before_inode = config.stat().st_ino
        before_mode = config.stat().st_mode
        # The executable stubs are the process boundary for the unmodified production
        # Docker and cp argv. Copy failure writes partial state before failing.
        scripts = {
            "docker": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert os.environ.get('PBS_REPOSITORY') == 'test'
assert 'host/plex/2026-10-09T00:00:00Z' == os.environ['SNAPSHOT']
if os.environ['PLEX_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
args=sys.argv[1:]
mount=args[args.index('--volume')+1]
assert mount.endswith(':/restore')
stage = Path(mount[:-9])
(stage / 'restored.txt').write_text('restored state\\n')
(stage / 'Preferences.xml').write_text('<Preferences PlexOnlineToken="restored token"/>')
(stage / 'Plug-in Support/Databases').mkdir(parents=True)
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['PLEX_TEST_CASE'] in ['copy-failure','rollback-failure'] else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['PLEX_TEST_CASE']=='move-failure' or (
    os.environ['PLEX_TEST_CASE']=='rollback-failure' and sys.argv[1].endswith('/state')):
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "chown": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['PLEX_TEST_CASE']=='ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown',*sys.argv[1:]]).returncode)
""",
            "publish": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[1] == 'restored token'
assert list(Path(os.environ['PLEX_TEST_ROOT']).glob('config/.plex-pre-restore-*/state/original.txt'))
if os.environ['PLEX_TEST_CASE']=='publish-failure':
    raise SystemExit(7)
Path(os.environ['PLEX_TEST_ROOT'],'published').touch()
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['plex']
with Path(os.environ['PLEX_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['PLEX_TEST_CASE']=='start-failure':
    raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = ("host/plex-other/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/plex/2026-10-09T00:00:00Z")
        server.token_failure = case == "token-failure"
        server.response_status = 503 if case == "health-failure" else 200
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "plex", "plex_project_dir": str(root), "plex_state_path": str(config),
            "restore_snapshot": snapshot, "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "app_config": {"app": {"config_path": str(config.parent), "mounts": [],
                                   "transcode_path": str(root / "transcode"), "puid": os.getuid(),
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
            "PLEX_TEST_CASE": case, "PLEX_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["success", "plan"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        boundary = {
            "copy-failure": "Copy the archived application state into place",
            "ownership-failure": "Restore application data ownership",
            "start-failure": "Start only Plex",
            "health-failure": "Wait for the restored service",
            "token-failure": "Verify the restored token through Plex",
            "publish-failure": "Publish the restored token for media consumers",
            "rollback-failure": "Put the original data back",
        }.get(case)
        if boundary:
            assert f"TASK [Plex restore | {boundary}]" in result.stdout
            assert "Move the original data into rollback space" in result.stdout
        rollback = list(config.parent.glob(".plex-pre-restore-*"))
        assert not list(config.parent.glob("plex-plex-restore-*"))
        if case == "rollback-failure":
            assert not config.exists()
            assert len(rollback) == 1
            config = rollback[0] / "state"
        else:
            assert not rollback, result.stdout + result.stderr
        assert config.exists()
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "restored.txt").read_text() == "restored state\n"
        else:
            assert (config / "original.txt").read_text() == "original state\n"
            assert not (config / "restored.txt").exists()
            assert config.stat().st_mode == before_mode
            assert config.stat().st_ino == before_inode, "original must be renamed, not copied"
            after = {str(p.relative_to(config)): (p.read_bytes(), p.stat().st_ino,
                                                p.stat().st_mode, p.stat().st_uid, p.stat().st_gid)
                     for p in config.rglob("*") if p.is_file()}
            assert after == original
        assert (root / "published").exists() == (case == "success")
        if case in ["plan", "foreign-group"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")
        if case == "foreign-group":
            assert "must belong to the requested host backup group" in result.stdout


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "extract-failure", "move-failure", "copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "token-failure", "publish-failure",
                     "rollback-failure", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Plex: read-only plan, group guard, successful restore and failure rollback passed")


if __name__ == "__main__":
    main()
