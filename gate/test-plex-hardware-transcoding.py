#!/usr/bin/env python3
"""Exercise Plex's device-driven hardware transcoding settings and convergence."""

import base64
import os
import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml

repo = Path(__file__).resolve().parents[1]
task_file = repo / "ansible/roles/plex/tasks/configure-transcoder.yml"


class PlexPreferences(BaseHTTPRequestHandler):
    def do_PUT(self):
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query)
        if parsed.path != "/:/prefs" or self.headers.get("X-Plex-Token") != "fixture-token":
            self.send_response(401)
            self.end_headers()
            return

        self.server.requests.append(values)
        tree = ET.parse(self.server.preferences_path)
        root = tree.getroot()
        for key in ["HardwareAcceleratedCodecs", "HardwareAcceleratedEncoders"]:
            root.set(key, values[key][0])
        tree.write(self.server.preferences_path, encoding="utf-8")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def run_playbook(server, preferences_path, playbook_path, devices):
    content = base64.b64encode(preferences_path.read_bytes()).decode()
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "app_config": {"app": {"devices": devices, "gpu_device": ""}},
            "_plex_base": f"http://127.0.0.1:{server.server_port}",
            "_plex_preferences": str(preferences_path),
            "_plex_preferences_after": {"content": content},
            "_plex_server_token": "fixture-token",
            "plex_http_retries": 5,
            "plex_http_delay": 0,
        },
        "tasks": [{"ansible.builtin.include_tasks": str(task_file)}],
    }]
    playbook_path.write_text(yaml.safe_dump(play, sort_keys=False))
    env = os.environ | {
        "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",
    }
    ansible = env.get("GATE_ANSIBLE_PLAYBOOK", str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"))
    result = subprocess.run(
        [ansible, "-i", "localhost,", "-c", "local", str(playbook_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "fixture-token" not in output, output
    recap = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", output)
    assert recap, output
    return int(recap.group(1)), output


def main():
    server = HTTPServer(("127.0.0.1", 0), PlexPreferences)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferences = root / "Preferences.xml"
            preferences.write_text('<Preferences PlexOnlineToken="fixture-token"/>')
            server.preferences_path = preferences
            playbook = root / "plan.yml"

            changed, _ = run_playbook(server, preferences, playbook, [])
            assert changed == 1
            assert server.requests[-1] == {
                "HardwareAcceleratedCodecs": ["0"],
                "HardwareAcceleratedEncoders": ["0"],
            }
            assert ET.parse(preferences).getroot().attrib["HardwareAcceleratedCodecs"] == "0"

            changed, _ = run_playbook(server, preferences, playbook, [])
            assert changed == 0
            assert len(server.requests) == 1

            changed, _ = run_playbook(server, preferences, playbook, ["/dev/dri"])
            assert changed == 1
            assert server.requests[-1] == {
                "HardwareAcceleratedCodecs": ["1"],
                "HardwareAcceleratedEncoders": ["1"],
            }
            assert ET.parse(preferences).getroot().attrib["HardwareAcceleratedEncoders"] == "1"

            changed, _ = run_playbook(server, preferences, playbook, ["/dev/dri"])
            assert changed == 0
            assert len(server.requests) == 2

            changed, _ = run_playbook(server, preferences, playbook, [])
            assert changed == 1
            assert server.requests[-1] == {
                "HardwareAcceleratedCodecs": ["0"],
                "HardwareAcceleratedEncoders": ["0"],
            }
            assert ET.parse(preferences).getroot().attrib["HardwareAcceleratedEncoders"] == "0"

            changed, _ = run_playbook(server, preferences, playbook, [])
            assert changed == 0
            assert len(server.requests) == 3
    finally:
        server.shutdown()
        server.server_close()
    print("Plex transcoding: missing settings converge to explicit off; declared /dev/dri enables hardware settings; repeats are unchanged")


if __name__ == "__main__":
    main()
