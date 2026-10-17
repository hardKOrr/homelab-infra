#!/usr/bin/env python3
"""Exercise Jellyfin's PBS backup and rollback-safe restore tasks locally."""

import copy
import http.server
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/jellyfin/tasks/backup.yml").read_text())
restore_source = yaml.safe_load((repo / "ansible/roles/jellyfin/tasks/restore.yml").read_text())
snapshot = "host/jellyfin/2026-10-10T00:00:00Z"
pre_restore_point = "host/jellyfin/2026-10-11T00:00:00Z"
source_password = "fixture Jellyfin password"


class JellyfinApi(http.server.BaseHTTPRequestHandler):
    def respond(self, status, value=None):
        body = b"" if value is None else json.dumps(value).encode()
        self.send_response(status)
        if value is not None:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/System/Info/Public":
            self.respond(self.server.health_status, {
                "ProductName": "Jellyfin Server",
                "StartupWizardCompleted": True,
            })
            return
        valid = self.headers.get("Authorization") == 'MediaBrowser Token="restored token"'
        if self.path == "/Users":
            self.respond(200 if valid else 401, [{"Name": "admin"}] if valid else None)
            return
        if self.path == "/Library/VirtualFolders":
            libraries = [{"Name": "Movies", "Locations": ["/media/library/movies"]}]
            if self.server.library_failure:
                libraries = []
            self.respond(200 if valid else 401, libraries if valid else None)
            return
        self.respond(404)

    def do_POST(self):
        if self.path != "/Users/AuthenticateByName":
            self.respond(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        valid = body == {"Username": "admin", "Pw": source_password}
        status = 200 if valid and not self.server.auth_failure else 401
        self.respond(status, {"AccessToken": "restored token"} if status == 200 else {})

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub PBS, Compose and Vault publication; run production file/API tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks", "")
        if include.endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "test",
                "k8s_pbs_fingerprint": "test",
                "k8s_pbs_password": "test",
            }
        elif include.endswith("upsert-item.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.command"] = {"argv": [str(root / "bin/publish")]}
        if "community.docker.docker_compose_v2" in task:
            compose = task.pop("community.docker.docker_compose_v2")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), compose["state"], *compose["services"]]
            }
            task["changed_when"] = True
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


def write_script(root, name, body):
    path = root / "bin" / name
    path.write_text(body)
    path.chmod(0o700)
    return path


def test_defaults_and_dispatch():
    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/jellyfin.yml").read_text())["jellyfin_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    restore_dispatch = (repo / "ansible/playbooks/maintenance/restore-app.yml").read_text()
    assert "when: _ra_app == 'jellyfin'" in restore_dispatch
    assert "restore_source_jellyfin_user:" in restore_dispatch
    assert "restore_source_jellyfin_password:" in restore_dispatch
    assert "'jellyfin']" in restore_dispatch


def check_backup(fail_upload):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        (config / "data").mkdir(parents=True)
        with sqlite3.connect(config / "data/jellyfin.db") as db:
            db.execute("create table canary(value text)")
            db.execute("insert into canary values ('library-user-config')")
        write_script(root, "docker", """#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
assert os.environ['PBS_REPOSITORY']=='test'
assert args[args.index('--volume')+1] == os.environ['JELLYFIN_TEST_ROOT']+'/config:/source:ro'
assert 'backup data.pxar:/source --backup-id "$BACKUP_ID"' in args[-1]
assert '--keep-last 2' in args[-1]
assert os.environ['BACKUP_ID']=='jellyfin'
raise SystemExit(7 if os.environ['FAIL_UPLOAD']=='1' else 0)
""")
        write_script(root, "service", """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['jellyfin']
with Path(os.environ['JELLYFIN_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
""")
        defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/jellyfin.yml").read_text())["jellyfin_defaults"]
        defaults["app"]["config_path"] = str(config)
        defaults["app"]["puid"] = os.getuid()
        defaults["app"]["pgid"] = os.getgid()
        playbook = root / "backup.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "jellyfin", "jellyfin_project_dir": str(root),
                     "app_config": defaults, "homelabinfra_infra": {"backups": {}}},
            "tasks": fixture_tasks(backup_source, root),
        }]))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "JELLYFIN_TEST_ROOT": str(root), "FAIL_UPLOAD": "1" if fail_upload else "0",
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                env=env, text=True, capture_output=True, check=False)
        assert (result.returncode != 0) == fail_upload, result.stdout + result.stderr
        assert (root / "services").read_text().splitlines() == ["stopped", "present"]


def check_restore(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "opt/jellyfin/config"
        (config / "data").mkdir(parents=True)
        (config / "original.txt").write_text("fresh deploy state\n")
        with sqlite3.connect(config / "data/jellyfin.db") as db:
            db.execute("create table original(value text)")
        write_script(root, "docker", """#!/usr/bin/env python3
import os,sqlite3,sys
from pathlib import Path
args=sys.argv[1:]
assert os.environ.get('PBS_REPOSITORY') == 'test'
if 'snapshot list' in args[-1]:
    print('host/jellyfin/2026-10-10T00:00:00Z')
else:
    assert os.environ['SNAPSHOT'] == 'host/jellyfin/2026-10-10T00:00:00Z'
    assert args[args.index('--tmpfs')+1] == '/tmp:rw,noexec,nosuid,nodev,size=256m'
    if os.environ['JELLYFIN_TEST_CASE']=='extract-failure': raise SystemExit(7)
    mount=args[args.index('--volume')+1]
    assert mount.endswith(':/restore')
    target=Path(mount[:-9])
    if os.environ['JELLYFIN_TEST_CASE']!='archive-missing-db':
        (target/'data').mkdir(parents=True,exist_ok=True)
        if os.environ['JELLYFIN_TEST_CASE']=='integrity-failure':
            (target/'data/jellyfin.db').write_text('not a sqlite database')
        else:
            with sqlite3.connect(target/'data/jellyfin.db') as db:
                db.execute('create table restored(value text)')
                db.execute("insert into restored values ('canary')")
    (target/'restored.txt').write_text('restored state\\n')
""")
        write_script(root, "service", """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['jellyfin']
with Path(os.environ['JELLYFIN_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['JELLYFIN_TEST_CASE']=='start-failure': raise SystemExit(7)
""")
        write_script(root, "cp", """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['JELLYFIN_TEST_CASE']=='copy-failure' else result.returncode)
""")
        write_script(root, "mv", """#!/usr/bin/env python3
import os,subprocess,sys
args=sys.argv[1:]
if os.environ['JELLYFIN_TEST_CASE']=='move-failure' and args[0].endswith('/config'): raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*args]).returncode)
""")
        write_script(root, "publish", """#!/usr/bin/env python3
import os
raise SystemExit(7 if os.environ['JELLYFIN_TEST_CASE']=='publish-failure' else 0)
""")
        server.health_status = 503 if case == "health-failure" else 200
        server.auth_failure = case == "auth-failure"
        server.library_failure = case == "library-failure"
        selected_snapshot = "host/foreign/2026-10-10T00:00:00Z" if case == "foreign-group" else snapshot
        prepoint = "" if case == "missing-prepoint" else pre_restore_point
        if case == "foreign-prepoint":
            prepoint = "host/foreign/2026-10-11T00:00:00Z"
        overwrite = case not in ["plan"]
        playbook = root / "restore.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {
                "instance": "jellyfin", "jellyfin_project_dir": str(root / "opt/jellyfin"),
                "jellyfin_client_name": "homelab-infra", "jellyfin_device_name": "deploy",
                "restore_snapshot": "" if case == "plan" else selected_snapshot,
                "restore_backup_id": "jellyfin", "restore_target_backup_id": "jellyfin",
                "restore_pre_restore_point": prepoint, "restore_destination": "existing",
                "restore_overwrite": overwrite,
                "restore_source_jellyfin_user": "admin",
                "restore_source_jellyfin_password": source_password,
                "jellyfin_http_retries": 1, "jellyfin_http_delay": 0,
                "homelabinfra_infra": {"backups": {}},
                "app_config": {
                    "app": {"config_path": str(config), "port": server.server_port,
                            "puid": os.getuid(), "pgid": os.getgid()},
                    "backup": {"enabled": True, "application_consistent": True,
                               "client": {"key_url": "test", "repository": "test",
                                          "suite": "test", "component": "test", "image": "test"}},
                },
            },
            "tasks": fixture_tasks(restore_source, root),
        }]))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "JELLYFIN_TEST_CASE": case, "JELLYFIN_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["plan", "success"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "restored.txt").read_text() == "restored state\n"
            with sqlite3.connect(config / "data/jellyfin.db") as db:
                assert db.execute("select value from restored").fetchone()[0] == "canary"
        else:
            assert (config / "original.txt").read_text() == "fresh deploy state\n"
        assert not list(config.parent.glob(".jellyfin-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group", "foreign-prepoint", "missing-prepoint"]:
            assert not (root / "services").exists(), "plan/rejected restore stopped Jellyfin"
        else:
            states = (root / "services").read_text().splitlines()
            if case in ["extract-failure", "archive-missing-db", "integrity-failure", "move-failure"]:
                assert states == ["stopped", "present"], states
            elif case == "success":
                assert states == ["stopped", "present"], states
            elif case in ["copy-failure", "start-failure", "health-failure", "auth-failure",
                          "library-failure", "publish-failure"]:
                assert states == (["stopped", "present", "stopped"]
                                  if case != "copy-failure" else ["stopped", "stopped"]), states
            else:
                raise AssertionError(f"unhandled restore case {case}")


def main():
    test_defaults_and_dispatch()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), JellyfinApi)
    server.health_status = 200
    server.auth_failure = False
    server.library_failure = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in [
            "plan", "foreign-group", "foreign-prepoint", "missing-prepoint",
            "extract-failure", "archive-missing-db", "integrity-failure", "move-failure",
            "copy-failure", "start-failure", "health-failure", "auth-failure",
            "library-failure", "publish-failure", "success",
        ]:
            check_restore(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    check_backup(False)
    check_backup(True)
    print("Jellyfin: native PBS backup, read-only planning, app/group guards, API verification, and restore rollback passed")


if __name__ == "__main__":
    main()
