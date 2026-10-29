#!/usr/bin/env python3
"""Run Plex's restore-swap hooks and check its swap inputs.

gate/test-restore-swap.py proves the swap and rollback. This test proves what Plex adds: the
staged point must hold Preferences.xml and the library databases, and the restored server
token must work against Plex before it is published.
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
role = repo / "ansible/roles/plex"


class Plex(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/library/sections"
        self.send_response(200 if self.headers.get("X-Plex-Token") == "restored token" else 401)
        self.end_headers()

    def log_message(self, *_args):
        pass


def run_hook(hook, stage, state, port):
    tasks = yaml.safe_load((role / f"tasks/restore-{hook}.yml").read_text())
    for task in tasks:
        task.pop("no_log", None)
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
            # Vaultwarden is the external boundary; publication must carry the restored token.
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.assert"] = {"that": [
                "vault_item_name == 'homelab-infra/media/plex'",
                "vault_item_fields == {'token': 'restored token'}"]}
    with tempfile.TemporaryDirectory() as directory:
        play = Path(directory) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "plex", "recovery_swap_stage": str(stage),
                     "plex_state_path": str(state), "app_config": {"app": {"port": port}}},
            "tasks": tasks,
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                              env=env, text=True, capture_output=True, check=False)


def main():
    swap = swap_inputs("plex")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ plex_state_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ plex_project_dir }}"
    assert swap["recovery_swap_health_url"].endswith("/identity")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Plex)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            data = stage / "data"
            data.mkdir(parents=True)
            assert run_hook("staged", stage, data, server.server_port).returncode != 0
            (data / "Preferences.xml").write_text("<Preferences/>")
            assert run_hook("staged", stage, data, server.server_port).returncode != 0, \
                "a point without the library databases must be refused"
            (data / "Plug-in Support/Databases").mkdir(parents=True)
            result = run_hook("staged", stage, data, server.server_port)
            assert result.returncode == 0, result.stdout + result.stderr

            for preferences, ok in [('<Preferences/>', False),
                                    ('<Preferences PlexOnlineToken=""/>', False),
                                    ('<Preferences PlexOnlineToken="wrong token"/>', False),
                                    ('<Preferences PlexOnlineToken="restored token"/>', True)]:
                (data / "Preferences.xml").write_text(preferences)
                result = run_hook("verified", stage, data, server.server_port)
                assert (result.returncode == 0) == ok, preferences + result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Plex: restore swap inputs, staged databases and restored token hooks passed")


if __name__ == "__main__":
    main()
