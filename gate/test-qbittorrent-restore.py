#!/usr/bin/env python3
"""Run qBittorrent's restore-swap hooks and check its swap inputs.

gate/test-restore-swap.py proves the swap and rollback. This test proves the two checks
qBittorrent adds: the staged point must hold qBittorrent.conf and BT_backup, and the
restored WebUI must accept the canonical credential.
"""

import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/qbittorrent"


class WebUI(http.server.BaseHTTPRequestHandler):
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


def run_hook(hook, stage, port):
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {
                "instance": "qbittorrent", "role_path": str(role), "recovery_swap_stage": str(stage),
                "app_config": {"app": {"port": port}},
                "homelabinfra_vault": {"media": {"qbittorrent": {
                    "username": "admin", "password": "fixture-password"}}},
            },
            "tasks": [{"ansible.builtin.include_tasks": str(role / f"tasks/restore-{hook}.yml")}],
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                              env=env, text=True, capture_output=True, check=False)


def main():
    swap = swap_inputs("qbittorrent")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), WebUI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "data/qBittorrent").mkdir(parents=True)
            (stage / "data/qBittorrent/qBittorrent.conf").write_text("[Preferences]\n")
            result = run_hook("staged", stage, server.server_port)
            assert result.returncode != 0, "a point without BT_backup must be refused"
            (stage / "data/qBittorrent/BT_backup").mkdir()
            result = run_hook("staged", stage, server.server_port)
            assert result.returncode == 0, result.stdout + result.stderr

            for reject, ok in [(True, False), (False, True)]:
                server.reject_login = reject
                result = run_hook("verified", stage, server.server_port)
                assert (result.returncode == 0) == ok, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("qBittorrent: restore swap inputs, staged-state and credential hooks passed")


if __name__ == "__main__":
    main()
