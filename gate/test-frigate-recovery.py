#!/usr/bin/env python3
"""Exercise Frigate's CPU-only templates and native restore rollback."""

import copy
import http.server
import os
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
restore_source = yaml.safe_load((repo / "ansible/roles/frigate/tasks/restore.yml").read_text())
backup_source = yaml.safe_load((repo / "ansible/roles/frigate/tasks/backup.yml").read_text())


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api/version"
        self.send_response(self.server.response_status)
        self.end_headers()
        self.wfile.write(b'{"version":"test"}')

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub only PBS resolution and Compose; execute production file/command tasks."""
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
        if task.get("name") == "Frigate restore | Recreate the config directory":
            task["ansible.builtin.file"]["owner"] = os.getuid()
            task["ansible.builtin.file"]["group"] = os.getgid()
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


def test_cpu_templates():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config_path = root / "frigate-config.yml"
        compose_path = root / "compose.yml"
        preserved_path = root / "saved-config.yml"
        preserved_path.write_text("ui_canary: preserved\n")
        play = [{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "vars": {
                "ansible_managed": "# generated",
                "instance": "frigate",
                "app_config": {"app": {
                    "detector": "cpu", "hwaccel_args": "", "mqtt": {}, "cameras": {},
                    "image": "frigate:test", "config_path": "/opt/frigate/config",
                    "recordings_path": "", "shm_size": "256m",
                    "port": 8971, "igpu_render_node": "", "coral_usb_device_path": "",
                }},
                "_frigate_rtsp_host_port": 8972,
                "_frigate_webrtc_host_port": 8973,
            },
            "tasks": [
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/config.yml.j2"),
                    "dest": str(config_path)}},
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/docker-compose.yml.j2"),
                    "dest": str(compose_path)}},
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/config.yml.j2"),
                    "dest": str(preserved_path), "force": False}},
            ],
        }]
        playbook = root / "render.yml"
        playbook.write_text(yaml.safe_dump(play))
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rendered_config = yaml.safe_load(config_path.read_text())
        rendered_compose = yaml.safe_load(compose_path.read_text())["services"]["frigate"]
        assert rendered_config["detectors"] == {"cpu1": {"type": "cpu"}}
        assert rendered_config["cameras"] == {}
        assert "/dev/bus/usb" not in rendered_compose["volumes"]
        assert "/media/frigate" not in rendered_compose["volumes"]
        assert "devices" not in rendered_compose
        assert preserved_path.read_text() == "ui_canary: preserved\n"

    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/frigate.yml").read_text())["frigate_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["backup"]["application_consistent"] is True
    role_tasks = yaml.safe_load((repo / "ansible/roles/frigate/tasks/main.yml").read_text())
    render = next(task for task in role_tasks if task.get("name") == "Frigate | Render the redacted Compose project and application config")
    assert render["ansible.builtin.template"]["force"] == "{{ item.force | default(true) | bool }}"
    config_template = next(item for item in render["loop"]
                           if item["dest"].endswith("/config.yml"))
    assert config_template["force"] is False


def test_deploy_preflight():
    playbook_source = yaml.safe_load((repo / "ansible/playbooks/apps/frigate.yml").read_text())
    guard = next(task for task in playbook_source[0]["pre_tasks"]
                 if task.get("name") == "Assert Frigate has an explicit detector and recording-storage contract")
    cases = [
        ({"cameras": {}, "recordings_path": "", "coral_usb_mapping": ""}, True),
        ({"cameras": {"front": {}}, "recordings_path": "", "coral_usb_mapping": ""}, False),
        ({"cameras": {}, "recordings_path": "/mnt/frigate-recordings", "coral_usb_mapping": ""}, False),
        ({"cameras": {}, "recordings_path": "", "coral_usb_mapping": "frigate-coral"}, False),
    ]
    for override, should_pass in cases:
        app = {
            "detector": "cpu", "recordings_path": "", "retention_days": 7,
            "gpu_device": "", "igpu_render_node": "", "coral_usb_mapping": "",
            "hwaccel_args": "", "cameras": {},
        } | override
        fixture = [{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"app_config": {"stack": "frigate", "app": app,
                                     "recording_storage": {}}},
            "tasks": [guard],
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight.yml"
            path.write_text(yaml.safe_dump(fixture))
            result = subprocess.run(
                [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                text=True, capture_output=True, check=False,
            )
        assert (result.returncode == 0) == should_pass, result.stdout + result.stderr


def check_restore(case, server):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        config.mkdir()
        (config / "config.yml").write_text("original config\n")
        (config / "original.txt").write_text("original state\n")
        before_mode = config.stat().st_mode
        scripts = {
            "docker": """#!/usr/bin/env python3
import os,sqlite3,sys
from pathlib import Path
assert os.environ.get('PBS_REPOSITORY') == 'test'
args=sys.argv[1:]
if 'snapshot list' in args[-1]:
    print('host/frigate/2026-10-09T00:00:00Z')
else:
    assert os.environ['SNAPSHOT'] == 'host/frigate/2026-10-09T00:00:00Z'
    if os.environ['FRIGATE_TEST_CASE']=='extract-failure':
        raise SystemExit(7)
    mount=args[args.index('--volume')+1]
    assert mount.endswith(':/restore')
    target=Path(mount[:-9])
    target.mkdir(parents=True,exist_ok=True)
    (target/'config.yml').write_text('detectors: {}\\n')
    db=sqlite3.connect(target/'frigate.db')
    db.execute('create table canary(name text)')
    db.execute("insert into canary values ('p2-canary-frigate-test')")
    db.commit();db.close()
""",
            "cp": """#!/usr/bin/env python3
import os,subprocess,sys
result=subprocess.run(['/bin/cp',*sys.argv[1:]])
raise SystemExit(7 if os.environ['FRIGATE_TEST_CASE']=='copy-failure' else result.returncode)
""",
            "mv": """#!/usr/bin/env python3
import os,subprocess,sys
if os.environ['FRIGATE_TEST_CASE']=='move-failure': raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv',*sys.argv[1:]]).returncode)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['frigate']
with Path(os.environ['FRIGATE_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
if sys.argv[1]=='present' and os.environ['FRIGATE_TEST_CASE']=='start-failure': raise SystemExit(7)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        snapshot = ("host/frigate-other/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/frigate/2026-10-09T00:00:00Z")
        server.response_status = 503 if case == "health-failure" else 200
        plays = [{
            "hosts": "localhost", "gather_facts": False,
            "vars": {
                "instance": "frigate", "frigate_project_dir": str(root),
                "frigate_http_retries": 1, "frigate_http_delay": 0,
                "restore_snapshot": "" if case == "plan" else snapshot,
                "restore_overwrite": case not in ["plan", "foreign-group"],
                "restore_pre_restore_point": "" if case == "missing-prepoint" else "host/frigate/2026-10-10T00:00:00Z",
                "homelabinfra_infra": {"backups": {}},
                "app_config": {
                    "app": {"config_path": str(config), "port": server.server_port},
                    "backup": {"enabled": True, "application_consistent": True,
                               "client": {"key_url": "test", "repository": "test",
                                          "suite": "test", "component": "test", "image": "test"}},
                },
            },
            "tasks": fixture_tasks(restore_source, root),
        }]
        playbook = root / "restore.yml"
        playbook.write_text(yaml.safe_dump(plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "FRIGATE_TEST_CASE": case, "FRIGATE_TEST_ROOT": str(root),
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                env=env, text=True, capture_output=True, check=False)
        success = case in ["success", "plan"]
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        assert config.exists()
        if case == "success":
            assert not (config / "original.txt").exists()
            assert (config / "config.yml").read_text() == "detectors: {}\n"
            with sqlite3.connect(config / "frigate.db") as db:
                assert db.execute("select name from canary").fetchone()[0] == "p2-canary-frigate-test"
        else:
            assert (config / "original.txt").read_text() == "original state\n"
            assert (config / "config.yml").read_text() == "original config\n"
            assert config.stat().st_mode == before_mode
        assert not list(root.glob(".frigate-pre-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group", "missing-prepoint"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")
        if case == "foreign-group":
            assert "must belong to host/frigate/" in result.stdout


def check_backup(fail_upload):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        config.mkdir()
        (config / "config.yml").write_text("detectors: {}\n")
        sqlite3.connect(config / "frigate.db").close()
        scripts = {
            "docker": """#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
assert args[args.index('--volume')+1] == os.environ['FRIGATE_TEST_ROOT']+'/config:/source:ro'
assert 'backup data.pxar:/source --backup-id "$BACKUP_ID"' in args[-1]
assert '--keep-last 2' in args[-1]
raise SystemExit(7 if os.environ['FAIL_UPLOAD']=='1' else 0)
""",
            "service": """#!/usr/bin/env python3
import os,sys
from pathlib import Path
assert sys.argv[2:] == ['frigate']
with Path(os.environ['FRIGATE_TEST_ROOT'],'services').open('a') as log:
    log.write(sys.argv[1]+'\\n')
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script)
            path.chmod(0o700)
        defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/frigate.yml").read_text())["frigate_defaults"]
        defaults["app"]["config_path"] = str(config)
        playbook = root / "backup.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "frigate", "frigate_project_dir": str(root),
                     "app_config": defaults, "homelabinfra_infra": {"backups": {}}},
            "tasks": fixture_tasks(backup_source, root),
        }]))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "FRIGATE_TEST_ROOT": str(root), "FAIL_UPLOAD": "1" if fail_upload else "0",
        }
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                env=env, text=True, capture_output=True, check=False)
        assert (result.returncode != 0) == fail_upload, result.stdout + result.stderr
        assert (root / "services").read_text().splitlines() == ["stopped", "present"]


def main():
    test_cpu_templates()
    test_deploy_preflight()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "missing-prepoint", "extract-failure",
                     "move-failure", "copy-failure", "start-failure", "health-failure", "success"]:
            check_restore(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    check_backup(False)
    check_backup(True)
    print("Frigate: CPU-only templates, PBS backup restart, read-only plan, group guard, and restore rollback passed")


if __name__ == "__main__":
    main()
