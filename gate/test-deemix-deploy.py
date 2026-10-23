#!/usr/bin/env python3
"""Exercise Deemix's Lidarr settings and secret-safe login tasks locally."""

import http.server
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
DEPLOY = yaml.safe_load((ROOT / "ansible/roles/deemix/tasks/main.yml").read_text())
AUTHENTIK = yaml.safe_load((ROOT / "ansible/tasks/wiring/authentik.yml").read_text())
DEEMIX_PLAYBOOK = yaml.safe_load((ROOT / "ansible/playbooks/apps/deemix.yml").read_text())


def task(name, tasks=DEPLOY):
    return next(item for item in tasks if item.get("name") == name)


class LoginAPI(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.server.request_body = json.loads(self.rfile.read(length))
        body = json.dumps(self.server.response_body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def run_playbook(path):
    return subprocess.run(
        [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )


def check_settings_reconcile():
    names = [
        "Check the Deemix settings file",
        "Require Deemix to have created its settings file",
        "Read the current Deemix settings",
        "Parse the current Deemix settings",
        "Require Deemix settings to be a JSON object",
        "Enable the folder structure required by Lidarr",
        "Apply the Lidarr-compatible Deemix setting",
    ]
    tasks = [task(name) for name in names]
    assert tasks[2].get("no_log") and tasks[3].get("no_log")
    setting = tasks[5]
    assert setting.get("no_log")
    assert "createSingleFolder" in setting["ansible.builtin.copy"]["content"]
    assert "combine" in setting["ansible.builtin.copy"]["content"]
    assert "createSingleFolder" in setting["when"]

    with tempfile.TemporaryDirectory(prefix="deemix-deploy-") as directory:
        root = Path(directory)
        config = root / "config.json"
        config.write_text(json.dumps({
            "createSingleFolder": False,
            "tracknameTemplate": "keep-custom-template",
            "downloadLocation": "/downloads",
        }))
        plays = [{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "app_config": {"app": {
                    "config_path": str(root),
                    "puid": os.getuid(),
                    "pgid": os.getgid(),
                }},
            },
            "tasks": tasks + tasks,
            "handlers": [{"name": "Restart deemix", "ansible.builtin.debug": {
                "msg": "restart requested",
            }}],
        }]
        playbook = root / "settings.yml"
        playbook.write_text(yaml.safe_dump(plays))
        result = run_playbook(playbook)
        assert result.returncode == 0, result.stdout + result.stderr
        saved = json.loads(config.read_text())
        assert saved["createSingleFolder"] is True
        assert saved["tracknameTemplate"] == "keep-custom-template"
        assert saved["downloadLocation"] == "/downloads"
        assert result.stdout.count("RUNNING HANDLER [Restart deemix]") == 1


def check_login_api():
    login = task("Sign in to Deezer with the stored ARL")
    require_login = task("Require Deemix to authenticate the stored ARL")
    assert login.get("no_log") is True
    assert require_login.get("no_log") is True
    assert login["ansible.builtin.uri"]["method"] == "POST"
    assert "/api/loginArl" in login["ansible.builtin.uri"]["url"]
    assert "_dmx_arl" in login["ansible.builtin.uri"]["body"]["arl"]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LoginAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        secret = "fixture-arl-never-print"
        for response, expected_rc in [
            ({"status": 1, "user": {"username": "fixture"}}, 0),
            ({"status": 0}, 2),
        ]:
            server.response_body = response
            server.request_body = None
            with tempfile.TemporaryDirectory(prefix="deemix-login-") as directory:
                root = Path(directory)
                plays = [{
                    "hosts": "localhost",
                    "gather_facts": False,
                    "vars": {
                        "_dmx_base": f"http://127.0.0.1:{server.server_port}",
                        "_dmx_arl": secret,
                    },
                    "tasks": [login, require_login, task("Report that Deemix signed in to Deezer")],
                }]
                playbook = root / "login.yml"
                playbook.write_text(yaml.safe_dump(plays))
                result = run_playbook(playbook)
                assert result.returncode == expected_rc, result.stdout + result.stderr
                assert server.request_body == {"arl": secret}
                assert secret not in result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def check_admin_binding_scope():
    play_vars = DEEMIX_PLAYBOOK[2]["vars"]
    assert "wiring_auth_users" in play_vars
    assert "sso.admin_user" in play_vars["wiring_auth_users"]
    occurrences = []
    for path in (ROOT / "ansible/playbooks/apps").glob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if any("wiring_auth_users" in str(play.get("vars", {})) for play in doc):
            occurrences.append(path.name)
    assert occurrences == ["deemix.yml"], occurrences

    text = str(AUTHENTIK)
    assert "policy_engine_mode" in text and "'any'" in text
    assert "core/users/?username=" in text
    assert "'user': _ak_auth_user_pk_by_name[item]" in text
    assert "core/groups/" not in text[text.index("Find configured access users"):]


check_settings_reconcile()
check_login_api()
check_admin_binding_scope()
