#!/usr/bin/env python3
"""Run Emby's restore-swap verified hook and check its swap inputs.

gate/test-restore-swap.py proves the swap and rollback. This test proves what Emby adds: the
restored server must identify as Emby, accept the source app-owned account and hold a media
library before that account is recorded for the target instance.
"""

import http.server
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/emby"


class Emby(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        case = self.server.case
        if self.path == "/emby/System/Info/Public":
            identity = {"Id": "fixture", "ServerName": "emby", "Version": "4.8"}
            self.reply(200, {} if case == "not-emby" else identity)
        else:
            assert self.path == "/emby/Library/VirtualFolders/Query"
            assert self.headers["X-Emby-Token"] == "fixture-token"
            self.reply(200, {"Items": [] if case == "no-library" else [{"Name": "Movies"}]})

    def do_POST(self):
        assert self.path == "/emby/Users/AuthenticateByName"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body == {"Username": "source-user", "Pw": "source-password"}
        if self.server.case == "wrong-account":
            self.reply(401, {})
        else:
            self.reply(200, {"AccessToken": "fixture-token"})

    def log_message(self, *_args):
        pass


def run_verified(port):
    tasks = yaml.safe_load((role / "tasks/restore-verified.yml").read_text())
    for task in tasks:
        task.pop("no_log", None)
        if "until" in task:
            task["retries"] = 1
            task["delay"] = 0
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
            # Vaultwarden is the external boundary; the source account is what gets recorded.
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.assert"] = {"that": [
                "vault_item_name == 'homelab-infra/media/emby'",
                "vault_item_fields == {'username': 'source-user', 'password': 'source-password'}"]}
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "emby", "emby_client_name": "homelab", "emby_device_name": "ansible",
                     "restore_source_emby_user": "source-user",
                     "restore_source_emby_password": "source-password",
                     "app_config": {"app": {"port": port}}},
            "tasks": tasks,
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                              env=env, text=True, capture_output=True, check=False)


def main():
    swap = swap_inputs("emby")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ emby_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/emby/System/Info/Public")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Emby)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["not-emby", "wrong-account", "no-library", "restored"]:
            server.case = case
            result = run_verified(server.server_port)
            assert (result.returncode == 0) == (case == "restored"), case + result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Emby: restore swap inputs and the identity, account and library hook passed")


if __name__ == "__main__":
    main()
