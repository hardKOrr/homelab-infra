#!/usr/bin/env python3
"""Exercise Jellyfin's PBS backup, its restore contract and its restore-swap hooks.

gate/test-restore-swap.py proves the swap and rollback themselves.
"""

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

from pbs_client_stub import stub_pbs_client
from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/jellyfin/tasks/backup.yml").read_text())
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
            self.respond(200, {
                "ProductName": "Jellyfin Server",
                "StartupWizardCompleted": not self.server.unconfigured,
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
        if stub_pbs_client(task, root / "bin/proxmox-backup-client"):
            pass
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


def check_backup(fail_upload, db_relative="data/jellyfin.db"):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        config = root / "config"
        (config / db_relative).parent.mkdir(parents=True)
        with sqlite3.connect(config / db_relative) as db:
            db.execute("create table canary(value text)")
            db.execute("insert into canary values ('library-user-config')")
        write_script(root, "proxmox-backup-client", """#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
assert args == ['backup', 'data.pxar:' + os.environ['JELLYFIN_TEST_ROOT'] + '/config'], args
raise SystemExit(7 if os.environ['FAIL_UPLOAD']=='1' else 0)
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


def check_backup_inputs_and_hook():
    """The shared PBS client stops, uploads and restarts; Jellyfin supplies its inputs and an
    integrity check that runs while it is stopped."""
    upload = next(t for t in backup_source if t["name"] == "Jellyfin backup | Stop and archive only Jellyfin")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "docker"
    assert upload["vars"]["pbs_client_compose_project"] == "{{ jellyfin_project_dir }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ instance }}"]
    assert upload["vars"]["pbs_client_quiesced_hook"] == "{{ role_path }}/tasks/backup-quiesced.yml"
    hook = yaml.safe_load((repo / "ansible/roles/jellyfin/tasks/backup-quiesced.yml").read_text())
    for healthy in [True, False]:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jellyfin.db"
            if healthy:
                sqlite3.connect(database).close()
            else:
                database.write_text("not a sqlite database")
            playbook = Path(directory) / "hook.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"_jellyfin_backup_database_path": str(database)},
                "tasks": hook,
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                    text=True, capture_output=True, check=False)
            assert (result.returncode == 0) == healthy, result.stdout + result.stderr


def run_hook(hook, stage, port):
    tasks = yaml.safe_load((repo / f"ansible/roles/jellyfin/tasks/restore-{hook}.yml").read_text())
    for task in tasks:
        task.pop("no_log", None)
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
            # Vaultwarden is the external boundary; the source account is what gets recorded.
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.assert"] = {"that": [
                "vault_item_name == 'homelab-infra/media/jellyfin'",
                f"vault_item_fields == {{'username': 'admin', 'password': '{source_password}'}}"]}
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "hook.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "jellyfin", "recovery_swap_stage": str(stage),
                     "jellyfin_client_name": "homelab-infra", "jellyfin_device_name": "deploy",
                     "restore_source_jellyfin_user": "admin",
                     "restore_source_jellyfin_password": source_password,
                     "app_config": {"app": {"port": port}}},
            "tasks": tasks,
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                              env=env, text=True, capture_output=True, check=False)


def check_restore_swap(server):
    """Jellyfin supplies its paths and checks; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("jellyfin")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ jellyfin_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/System/Info/Public")

    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        (stage / "data").mkdir()
        assert run_hook("staged", stage, server.server_port).returncode != 0, "no jellyfin.db"
        database = stage / "data/data/jellyfin.db"
        database.parent.mkdir()
        database.write_text("not a sqlite database")
        assert run_hook("staged", stage, server.server_port).returncode != 0, "corrupt jellyfin.db"
        database.unlink()
        sqlite3.connect(database).close()
        result = run_hook("staged", stage, server.server_port)
        assert result.returncode == 0, result.stdout + result.stderr
        (stage / "data/jellyfin.db").write_bytes(database.read_bytes())
        assert run_hook("staged", stage, server.server_port).returncode != 0, "two jellyfin.db"

    for case in ["not-configured", "auth-failure", "library-failure", "restored"]:
        server.unconfigured = case == "not-configured"
        server.auth_failure = case == "auth-failure"
        server.library_failure = case == "library-failure"
        result = run_hook("verified", Path("/nonexistent"), server.server_port)
        assert (result.returncode == 0) == (case == "restored"), case + result.stdout + result.stderr


def check_contract():
    """An existing target needs an independent pre-restore point before anything runs."""
    contract = yaml.safe_load((repo / "ansible/roles/jellyfin/tasks/restore.yml").read_text())[0]
    for point, ok in [("", False), (snapshot, False), (pre_restore_point, True)]:
        with tempfile.TemporaryDirectory() as directory:
            playbook = Path(directory) / "contract.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"restore_overwrite": True, "restore_snapshot": snapshot,
                         "restore_pre_restore_point": point, "restore_destination": "existing",
                         "restore_source_jellyfin_user": "admin",
                         "restore_source_jellyfin_password": source_password,
                         "homelabinfra_infra": {"backups": {}},
                         "app_config": {"backup": {"enabled": True, "application_consistent": True}}},
                "tasks": [contract],
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                    text=True, capture_output=True, check=False)
            assert (result.returncode == 0) == ok, point + result.stdout + result.stderr


def main():
    test_defaults_and_dispatch()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), JellyfinApi)
    server.auth_failure = False
    server.library_failure = False
    server.unconfigured = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        check_restore_swap(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    check_backup(False)
    check_backup(True)
    check_backup(False, "data/data/jellyfin.db")
    check_backup_inputs_and_hook()
    check_contract()
    print("Jellyfin: PBS backup, restore contract, swap inputs, staged database and API verification hooks passed")


if __name__ == "__main__":
    main()
