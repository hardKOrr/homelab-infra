#!/usr/bin/env python3
"""Exercise Plex capability/dispatch and the real role's non-mutating restore plan."""

import base64
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
    assert config["routing"]["identity"] == "none"
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
            source = yaml.safe_load((repo / "ansible/roles/plex/tasks/main.yml").read_text())
            extract = next(task for task in source if task["name"] ==
                           "Extract the Plex server token for media consumers")
            token_guard = next(task for task in source if task["name"] ==
                               "Assert Plex exchanged the claim token for a server token")
            restore = yaml.safe_load((repo / "ansible/roles/plex/tasks/restore.yml").read_text())
            play = [{
                "hosts": "localhost", "gather_facts": False,
                "vars": {
                    "instance": "plex", "app_config": config,
                    "homelabinfra_infra": {"backups": {
                        "host": f"http://127.0.0.1:{server.server_port}",
                        "datastore": "fixture", "api_token_id": "fixture",
                        "api_token_secret": "fixture"}},
                    "restore_overwrite": False, "restore_snapshot": "host/plex/fixture",
                    "_plex_preferences_after": {"content": base64.b64encode(
                        b'<Preferences PlexOnlineToken="fixture-server-token"/>').decode()},
                    "recovery_app_config": config, "recovery_app": "plex",
                    "recovery_instance": "plex", "recovery_operation": "restore",
                },
                "tasks": [
                    extract,
                    token_guard,
                    {"ansible.builtin.assert": {"that": ["_plex_server_token == 'fixture-server-token'"]}},
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                    {"ansible.builtin.include_role": {"name": "plex", "tasks_from": "restore"}},
                    {"ansible.builtin.assert": {"that": [
                        "not (_application_restore_complete | default(false))",
                        "_plex_restore_tmp is skipped"]}},
                ],
            }]
            path = Path(directory) / "plan.yml"
            env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                                "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles")}

            def run(tasks, overrides=None, failure=None):
                candidate = [{**play[0], "vars": play[0]["vars"] | (overrides or {}), "tasks": tasks}]
                path.write_text(yaml.safe_dump(candidate))
                result = subprocess.run([str(dispatch.ansible), "-i", "localhost,", "-c", "local", str(path)],
                                        env=env, capture_output=True, text=True, check=False)
                output = result.stdout + result.stderr
                if failure:
                    assert result.returncode != 0 and failure in output, output
                else:
                    assert result.returncode == 0, output

            run(play[0]["tasks"])
            run([{"ansible.builtin.include_role": {"name": "plex", "tasks_from": "restore"}}],
                {"restore_backup_id": "plex-source", "restore_snapshot": "host/plex-source/fixture"})
            # The production role must reject a different group before stopping Plex.
            for snapshot in ["host/another-app/fixture", "host/plex-copy/fixture"]:
                run([{"ansible.builtin.include_role": {"name": "plex", "tasks_from": "restore"}}],
                    {"restore_overwrite": True, "restore_snapshot": snapshot},
                    "must belong to the requested host backup group")
            # Exercise the actual tempfile task without invoking Docker or replacing state.
            staging = next(task for task in restore if task["name"] ==
                           "Plex restore | Create a private staging directory")
            run([staging, {"ansible.builtin.assert": {"that": [
                "(_plex_restore_tmp.path | dirname) == app_config.app.config_path"]}},
                 {"ansible.builtin.file": {"path": "{{ _plex_restore_tmp.path }}", "state": "absent"}}],
                {"restore_overwrite": True})
            restored_tasks = next(task["block"] for task in restore if "block" in task)
            restored_extract = next(task for task in restored_tasks if task["name"] ==
                                    "Plex restore | Resolve the restored server token")
            restored_guard = next(task for task in restored_tasks if task["name"] ==
                                  "Plex restore | Require the restored server token")
            for preferences in [b'<Preferences/>', b'<Preferences PlexOnlineToken=""/>']:
                content = {"content": base64.b64encode(preferences).decode()}
                run([extract, token_guard], {"_plex_preferences_after": content}, "lacks PlexOnlineToken")
                run([restored_extract, restored_guard], {"_plex_restored_preferences": content},
                    "lacks PlexOnlineToken")
            assert sentinel.read_text() == "preserve me"
    finally:
        server.shutdown()
        server.server_close()
    print("Plex recovery: dispatch, preview, source group, config-volume staging and token guards passed")


if __name__ == "__main__":
    main()
