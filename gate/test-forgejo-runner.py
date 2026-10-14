#!/usr/bin/env python3
"""Exercise Forgejo Runner removal with user and global registrations."""

import http.server
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
remove_tasks = yaml.safe_load(
    (repo / "ansible/tasks/app-wiring/forgejo-runner-remove.yml").read_text()
)


class RunnerApiState:
    def __init__(self, scope):
        self.scope = scope
        self.deletes = []
        self.authorizations = []


class RunnerApi(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        state = self.server.state
        state.authorizations.append(self.headers.get("Authorization", ""))
        if self.path == f"/api/v1/{state.scope}/actions/runners":
            body = json.dumps({
                "runners": [
                    {"id": 41, "name": "forgejo-runner"},
                    {"id": 42, "name": "other-runner"},
                ]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_DELETE(self):
        state = self.server.state
        state.authorizations.append(self.headers.get("Authorization", ""))
        state.deletes.append(self.path)
        if self.path == f"/api/v1/{state.scope}/actions/runners/41":
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def log_message(self, *_args):
        pass


def run_removal(scope, token_field, include_admin_fallback=False):
    token = f"fixture-{scope}-token"
    secrets = {token}
    vault_fields = {token_field: token}
    if include_admin_fallback:
        vault_fields["admin_api_token"] = "fixture-admin-token"
        secrets.add(vault_fields["admin_api_token"])
    state = RunnerApiState(scope)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RunnerApi)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="forgejo-runner-remove-") as temp:
            playbook = Path(temp) / "remove.yml"
            playbook.write_text(yaml.safe_dump([{
                "name": "Forgejo Runner removal test",
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {
                    "runner_remove_instance": "forgejo-runner",
                    "runner_remove_forgejo_instance": "forgejo",
                    "homelabinfra_infra": {
                        "apps": {"forgejo": {"url": f"http://127.0.0.1:{server.server_port}/"}}
                    },
                    "homelabinfra_vault": {
                        "apps": {"forgejo": vault_fields}
                    },
                },
                "tasks": remove_tasks,
            }]))
            result = subprocess.run(
                [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                env=os.environ | {"ANSIBLE_NOCOLOR": "1"},
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode == 0, output
            assert all(secret not in output for secret in secrets), (
                "API token appeared in Ansible output"
            )
        expected = f"/api/v1/{scope}/actions/runners/41"
        assert state.deletes == [expected], state.deletes
        assert state.authorizations == [f"token {token}", f"token {token}"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def main():
    run_removal("user", "runner_api_token", include_admin_fallback=True)
    run_removal("admin", "admin_api_token")
    print("Forgejo Runner removal checks passed")


if __name__ == "__main__":
    main()
