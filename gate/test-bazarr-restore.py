#!/usr/bin/env python3
"""Run Bazarr's restore-swap hooks and check its swap inputs.

gate/test-restore-swap.py proves the swap and rollback. This test proves what Bazarr adds:
the staged point must hold the API key Deploy reads, and the restored server must accept it
before it is published.
"""

import base64
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
role = repo / "ansible/roles/bazarr"


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api/system/settings"
        assert self.headers.get("X-API-KEY") == "fixture-key"
        self.send_response(self.server.response_status)
        self.end_headers()

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
            "vars": {"instance": "bazarr", "recovery_swap_stage": str(stage),
                     "_bazarr_api_key": "fixture-key", "app_config": {"app": {"port": port}}},
            "tasks": tasks + list(extra_tasks),
        }]))
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                              env=env, text=True, capture_output=True, check=False)


def check_swap(server):
    swap = swap_inputs("bazarr")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "/opt/{{ instance }}"
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        result = run_hook("staged", stage, server.server_port)
        assert result.returncode != 0, "a point without config.yaml must be refused"
        (stage / "data/config").mkdir(parents=True)
        (stage / "data/config/config.yaml").write_text("general: {}\n")
        result = run_hook("staged", stage, server.server_port)
        assert result.returncode != 0, "a point without an API key must be refused"
        (stage / "data/config/config.yaml").write_text("auth:\n  apikey: fixture-key\n")
        result = run_hook("staged", stage, server.server_port, [
            {"ansible.builtin.assert": {"that": ["_bazarr_api_key == 'fixture-key'"]}}])
        assert result.returncode == 0, result.stdout + result.stderr
        for status, ok in [(503, False), (200, True)]:
            server.response_status = status
            result = run_hook("verified", stage, server.server_port)
            assert (result.returncode == 0) == ok, result.stdout + result.stderr


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Deploy and Restore must read the same nested YAML credential.
        deploy = yaml.safe_load((repo / "ansible/roles/bazarr/tasks/main.yml").read_text())
        restore = yaml.safe_load((role / "tasks/restore-staged.yml").read_text())
        for tasks, extract_name, guard_name in [
            (deploy, "Extract the Bazarr API key", "Assert Bazarr generated an API key"),
            (restore, "Bazarr restore | Extract the archived API key",
             "Bazarr restore | Require the archived API key"),
        ]:
            extract = next(t for t in tasks if t["name"] == extract_name)
            guard = next(t for t in tasks if t["name"] == guard_name)
            for config, expected in [("auth:\n  apikey: fixture-key\n", "fixture-key"),
                                     ("general: {}\n", ""), ("auth: {}\n", "")]:
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "credential.yml"
                    path.write_text(yaml.safe_dump([{
                        "hosts": "localhost", "gather_facts": False,
                        "vars": {"_bazarr_config": {"content": base64.b64encode(
                            config.encode()).decode()}},
                        "tasks": [extract, {"ansible.builtin.assert": {
                            "that": [f"_bazarr_api_key == '{expected}'"]}}, {
                            "block": [guard, {"ansible.builtin.assert": {"that": [bool(expected)]}}],
                            "rescue": [{"ansible.builtin.assert": {"that": [
                                not bool(expected),
                                f"ansible_failed_task.name == '{guard_name}'",
                                "_bazarr_api_key == ''",
                            ]}}],
                        }],
                    }]))
                    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                            text=True, capture_output=True, check=False)
                    assert result.returncode == 0, result.stdout + result.stderr
        check_swap(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Bazarr: restore swap inputs, staged API key and authenticated settings hooks passed")


if __name__ == "__main__":
    main()
