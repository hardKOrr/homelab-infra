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

from pbs_client_stub import stub_pbs_client

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
    """Stub the PBS client and Compose; run the production file/command tasks."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        task.pop("no_log", None)
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
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
assert args[:2] == ['restore', 'host/deemix/2026-10-09T00:00:00Z'], args
if os.environ['DEEMIX_TEST_CASE'] == 'extract-failure':
    raise SystemExit(7)
assert args[2] == 'data.pxar'
staging=Path(args[3])
Path(staging, 'restored.txt').write_text('restored state\\n')
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
        snapshot = "host/deemix/2026-10-09T00:00:00Z"
        server.response_status = 503 if case == "health-failure" else 200
        plays = [{"hosts": "localhost", "gather_facts": False, "vars": {
            "instance": "deemix", "deemix_project_dir": str(root),
            "restore_snapshot": snapshot, "restore_overwrite": case != "plan",
            "homelabinfra_infra": {"backups": {}},
            "app_config": {"app": {"config_path": str(config), "puid": os.getuid(),
                                   "pgid": os.getgid(), "port": server.server_port},
                           "backup": {"enabled": True, "application_consistent": True}},
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
        if case == "plan":
            assert not (root / "services").exists(), "read-only restore stopped service"
        else:
            states = (root / "services").read_text().splitlines()
            assert states[0] == "stopped"
            assert states[-1] == ("present" if success else "stopped")


def check_backup():
    """The shared PBS client stops, uploads and restarts; Deemix supplies only its inputs."""
    tasks = yaml.safe_load((repo / "ansible/roles/deemix/tasks/backup.yml").read_text())
    upload = next(t for t in tasks if t["name"] == "Deemix backup | Quiesce Deemix and archive its config to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "docker"
    assert upload["vars"]["pbs_client_compose_project"] == "{{ deemix_project_dir }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ instance }}"]
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ app_config.app.config_path }}"}]


def check_arl():
    deploy = yaml.safe_load((repo / "ansible/roles/deemix/tasks/main.yml").read_text())
    names = ["Resolve the app kind and ARL", "Check the existing Deezer ARL file",
             "Read the preserved Deezer ARL", "Adopt the preserved Deezer ARL for media wiring",
             "Seed the Deezer ARL", "Store and verify the ARL"]
    for original, configured, expected in [(None, "", ""), ("", "", ""),
                                           ("restored cookie", "", "restored cookie"),
                                           ("restored cookie", "rotated cookie", "rotated cookie")]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text('{"tracknameTemplate":"canary"}')
            cookie = root / ".arl"
            if original is not None:
                cookie.write_text(original)
                cookie.chmod(0o600)
            before = cookie.stat() if original is not None else None
            tasks = [copy.deepcopy(next(t for t in deploy if t["name"] == name)) for name in names]
            # Stub only Vaultwarden transport, retaining the production publication vars.
            publish = tasks[-1]
            publish.pop("ansible.builtin.include_tasks")
            publish["ansible.builtin.set_fact"] = {"published_arl": "{{ vault_item_fields.arl }}"}
            tasks[-2]["register"] = "seed_result"
            verify = {"ansible.builtin.assert": {"that": [
                "published_arl == expected_arl", "_dmx_arl == expected_arl"]}}
            path = root / "arl.yml"
            path.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"instance": "deemix", "expected_arl": expected,
                         "app_config": {"app": {"config_path": str(root), "arl": configured,
                                                "media_kind": "deemix", "port": 6595,
                                                "puid": os.getuid(), "pgid": os.getgid()}}},
                "tasks": tasks + [verify] + copy.deepcopy(tasks) + [verify, {
                    "ansible.builtin.assert": {"that": ["not seed_result.changed"]}}],
                "handlers": [{"name": "Restart deemix", "ansible.builtin.debug": {"msg": "restart"}}],
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                    text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
            assert cookie.read_text() == expected
            if original is not None and not configured:
                assert cookie.stat().st_mode == before.st_mode
                assert cookie.stat().st_mtime_ns == before.st_mtime_ns
            restarted = "RUNNING HANDLER [Restart deemix]" in result.stdout
            assert restarted == (original is None or bool(configured and configured != original))
            assert settings.read_text() == '{"tracknameTemplate":"canary"}'


def main():
    check_arl()
    check_backup()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "extract-failure", "move-failure", "copy-failure",
                     "ownership-failure", "start-failure", "health-failure", "rollback-failure", "success"]:
            check(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Deemix: read-only plan, successful restore and failure rollback passed")


if __name__ == "__main__":
    main()
