#!/usr/bin/env python3
"""Run SABnzbd's restore-swap hooks and check its swap inputs.

gate/test-restore-swap.py proves the swap and rollback. This test proves what SABnzbd adds:
the staged point must hold sabnzbd.ini with an API key, and the restored queue API must
accept that key before it is published.
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
role = repo / "ansible/roles/sabnzbd"


class Queue(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api?mode=queue&output=json&apikey=fixture-key"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "API Key Incorrect"}' if self.server.reject_key else b'{"queue": {}}')

    def log_message(self, *_args):
        pass


def run_hook(hook, stage, port, extra_tasks=()):
    tasks = yaml.safe_load((role / f"tasks/restore-{hook}.yml").read_text())
    for task in tasks:
        task.pop("no_log", None)
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
            # Vaultwarden is the external boundary; publication must carry the archived key.
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.assert"] = {"that": ["vault_item_fields.api_key == 'fixture-key'"]}
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "sabnzbd", "recovery_swap_stage": str(stage),
                     "_sabnzbd_api_key": "fixture-key", "app_config": {"app": {"port": port}}},
            "tasks": tasks + list(extra_tasks),
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                              env=env, text=True, capture_output=True, check=False)


def main():
    swap = swap_inputs("sabnzbd")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ sabnzbd_project_dir }}"

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Queue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            result = run_hook("staged", stage, server.server_port)
            assert result.returncode != 0, "a point without sabnzbd.ini must be refused"
            (stage / "data").mkdir()
            for ini in ["[misc]\nhost = 0.0.0.0\n", "[misc]\napi_key = \n"]:
                (stage / "data/sabnzbd.ini").write_text(ini)
                result = run_hook("staged", stage, server.server_port)
                assert result.returncode != 0, "a point without an API key must be refused"
            (stage / "data/sabnzbd.ini").write_text("[misc]\n  api_key = fixture-key  \n")
            result = run_hook("staged", stage, server.server_port, [
                {"ansible.builtin.assert": {"that": ["_sabnzbd_api_key == 'fixture-key'"]}}])
            assert result.returncode == 0, result.stdout + result.stderr
            for reject, ok in [(True, False), (False, True)]:
                server.reject_key = reject
                result = run_hook("verified", stage, server.server_port)
                assert (result.returncode == 0) == ok, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("SABnzbd: restore swap inputs, staged API key and authenticated queue hooks passed")


if __name__ == "__main__":
    main()
