#!/usr/bin/env python3
"""Check Tautulli's Plex connectivity check and its restore swap inputs."""

import copy
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


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/identity":
            valid = self.headers.get("X-Plex-Token") == "fixture token with spaces"
            self.send_response(200 if valid else 401)
            self.end_headers()
            self.wfile.write(b'<MediaContainer machineIdentifier="fixture"/>')
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        pass


def check_swap():
    """Tautulli supplies its paths and health URL; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("tautulli")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ tautulli_project_dir }}"
    assert swap["recovery_swap_health_url"] == "http://localhost:{{ app_config.app.port }}"


def check_plex_endpoint():
    """Plex on this host uses the container name; Plex elsewhere uses its host and port."""
    tasks = yaml.safe_load((repo / "ansible/roles/tautulli/tasks/plex-endpoint.yml").read_text())
    names = ("Choose a reachable Plex host", "Resolve the runtime and discovery URLs",
             "Resolve the runtime URL")
    picked = [t for t in tasks if t["name"].split(" | ")[1] in names]
    assert len(picked) == 3, picked
    cases = [
        ("tautulli-host", {"port": 32400}, "plex", "32400",
         "http://plex:32400", "http://10.0.0.5:32400/identity"),
        ("other-host", {"port": 32401}, "10.0.0.5", "32401",
         "http://10.0.0.5:32401", "http://10.0.0.5:32401/identity"),
        ("tautulli-host", {"port": 32401, "container_port": 32500}, "plex", "32500",
         "http://plex:32500", "http://10.0.0.5:32401/identity"),
        ("other-host", {"port": 32401, "container_port": 32500}, "10.0.0.5", "32401",
         "http://10.0.0.5:32401", "http://10.0.0.5:32401/identity"),
    ]
    for resolved, app, host, port, url, discovery in cases:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "endpoint.yml"
            path.write_text(yaml.safe_dump([{
                "hosts": "tautulli-host", "gather_facts": False,
                "vars": {"resolve_stack_host": resolved, "_tautulli_plex_instance": "plex",
                         "_tautulli_plex_config": {"app": app}},
                "tasks": picked + [{"ansible.builtin.assert": {"that": [
                    f"_tautulli_plex_host == '{host}'", f"_tautulli_plex_url == '{url}'",
                    f"_tautulli_plex_runtime_port | string == '{port}'",
                    f"_tautulli_plex_discovery_url == '{discovery}'"]}}],
            }]))
            inventory = Path(directory) / "inventory.yml"
            inventory.write_text(yaml.safe_dump({"all": {"hosts": {
                name: {"ansible_connection": "local", "ansible_host": "10.0.0.5"}
                for name in ("tautulli-host", "other-host")}}}))
            result = subprocess.run([str(ansible), "-i", str(inventory), str(path)],
                                    env=os.environ | {"ANSIBLE_NOCOLOR": "1"}, text=True,
                                    capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Run the deployment check's exact curl argv through Ansible against an
        # authenticated local endpoint; only the Docker exec boundary is substituted.
        deploy = yaml.safe_load((repo / "ansible/roles/tautulli/tasks/main.yml").read_text())
        task = copy.deepcopy(next(t for t in deploy if t["name"] ==
                                  "Verify Tautulli can reach the selected Plex container"))
        task["ansible.builtin.command"] = {
            "argv": task.pop("community.docker.docker_container_exec")["argv"]}
        task["retries"] = 1
        task["delay"] = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "connectivity.yml"
            path.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"_tautulli_plex_url": f"http://127.0.0.1:{server.server_port}",
                         "_tautulli_plex_token": "fixture token with spaces"},
                "tasks": [task],
            }]))
            env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                    env=env, text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
        check_swap()
        check_plex_endpoint()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Tautulli: Plex connectivity check and restore swap inputs passed")


if __name__ == "__main__":
    main()
