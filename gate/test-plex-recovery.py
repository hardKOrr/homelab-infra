#!/usr/bin/env python3
"""Exercise Plex capability/dispatch and the real role's non-mutating restore plan."""

import importlib.util
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dispatch", repo / "gate/test-recovery-dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


class Certificate(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"fingerprint": "fixture"}]}).encode())

    def log_message(self, *_):
        pass


def main():
    for instance in ["plex", "plex-copy"]:
        dispatch.check("backup-app.yml", "Publish the application backup dispatch across plays",
                       {"instance": instance, "app": "plex"}, {"_ba_app": "plex"})
        dispatch.check("restore-app.yml", "Publish the application restore dispatch across plays",
                       {"instance": instance, "app": "plex", "snapshot": "host/plex/fixture"},
                       {"_ra_app": "plex", "_ra_target": instance, "_ra_overwrite": False,
                        "_ra_recovery_point": "host/plex/fixture"})
    config = yaml.safe_load((repo / "ansible/vars/app-defaults/plex.yml").read_text())["plex_defaults"]
    catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())
    assert {"backup", "restore"} <= set(catalog["applications"]["plex"]["actions"])
    server = HTTPServer(("127.0.0.1", 0), Certificate)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            config["app"]["config_path"] = directory
            config["app"]["mounts"] = []
            sentinel = Path(directory) / "Preferences.xml"
            sentinel.write_text("preserve me")
            play = [{
                "hosts": "localhost", "gather_facts": False,
                "vars": {
                    "instance": "plex", "app_config": config,
                    "homelabinfra_infra": {"backups": {
                        "host": f"http://127.0.0.1:{server.server_port}",
                        "datastore": "fixture", "api_token_id": "fixture",
                        "api_token_secret": "fixture"}},
                    "restore_overwrite": False, "restore_snapshot": "host/plex/fixture",
                    "recovery_app_config": config, "recovery_app": "plex",
                    "recovery_instance": "plex", "recovery_operation": "restore",
                },
                "tasks": [
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                    {"ansible.builtin.include_role": {"name": "plex", "tasks_from": "restore"}},
                    {"ansible.builtin.assert": {"that": [
                        "not (_application_restore_complete | default(false))",
                        "_plex_restore_tmp is skipped"]}},
                ],
            }]
            path = Path(directory) / "plan.yml"
            path.write_text(yaml.safe_dump(play))
            env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                                "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles")}
            result = subprocess.run([str(dispatch.ansible), "-i", "localhost,", "-c", "local", str(path)],
                                    env=env, capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
            assert sentinel.read_text() == "preserve me"
    finally:
        server.shutdown()
        server.server_close()
    print("Plex recovery: native dispatch and read-only restore guard passed")


if __name__ == "__main__":
    main()
