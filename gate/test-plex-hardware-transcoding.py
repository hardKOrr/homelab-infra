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
device_task_file = repo / "ansible/roles/plex/tasks/configure-devices.yml"
compose_template = repo / "ansible/roles/plex/templates/docker-compose.yml.j2"


class PlexPreferences(BaseHTTPRequestHandler):
    def do_PUT(self):
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query)
        if parsed.path != "/:/prefs" or self.headers.get("X-Plex-Token") != "fixture-token":
            self.send_response(401)
            self.end_headers()
            return

        self.server.requests.append(values)

        def flush_preferences():
            tree = ET.parse(self.server.preferences_path)
            root = tree.getroot()
            for key in ["HardwareAcceleratedCodecs", "HardwareAcceleratedEncoders"]:
                root.set(key, values[key][0])
            tree.write(self.server.preferences_path, encoding="utf-8")

        pending = threading.Timer(self.server.flush_delay, flush_preferences)
        self.server.pending_writes.append(pending)
        pending.start()
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def run_playbook(server, preferences_path, playbook_path, devices, gpu_device="", expect_retry=False):
    content = base64.b64encode(preferences_path.read_bytes()).decode()
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "app_config": {"app": {"devices": devices, "gpu_device": gpu_device}},
            "_plex_base": f"http://127.0.0.1:{server.server_port}",
            "_plex_preferences": str(preferences_path),
            "_plex_preferences_after": {"content": content},
            "_plex_server_token": "fixture-token",
            "plex_http_retries": 40,
            "plex_http_delay": 0.1,
        },
        "tasks": [{"ansible.builtin.include_tasks": str(task_file)}],
    }]
    if expect_retry:
        play[0]["tasks"].append({
            "name": "Confirm Plex preferences were reread after the delayed flush",
            "ansible.builtin.assert": {
                "that": "_plex_preferences_hardware_after.attempts | default(1) | int > 1",
                "fail_msg": "Plex readback did not retry while its preferences were stale.",
            },
        })
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


def run_device_access(root, devices, gpu_device, expected_groups):
    fake_bin = root / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_find = fake_bin / "find"
    fake_find.write_text("#!/bin/sh\nprintf '44\\n104\\n104\\n'\n")
    fake_find.chmod(0o755)
    playbook_path = root / "configure-devices.yml"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "app_config": {"app": {"devices": devices, "gpu_device": gpu_device}},
            "expected_groups": expected_groups,
        },
        "tasks": [
            {"ansible.builtin.include_tasks": str(device_task_file)},
            {
                "name": "Assert Plex receives the host DRI groups",
                "ansible.builtin.assert": {
                    "that": "_plex_dri_group_ids == expected_groups",
                    "fail_msg": "Plex DRI group resolution did not match the fixture.",
                },
            },
        ],
    }]
    playbook_path.write_text(yaml.safe_dump(play, sort_keys=False))
    env = os.environ | {
        "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    ansible = env.get("GATE_ANSIBLE_PLAYBOOK", str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"))
    result = subprocess.run(
        [ansible, "-i", "localhost,", "-c", "local", str(playbook_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output


def render_compose(root, devices, gpu_device, group_ids):
    compose_path = root / "docker-compose.yml"
    playbook_path = root / "render-compose.yml"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance": "plex",
            "app_config": {
                "stack": "gpu-stack",
                "app": {
                    "image": "plexinc/pms-docker:latest",
                    "port": 32400,
                    "container_port": 32400,
                    "config_path": "/opt/plex/config",
                    "transcode_path": "/opt/plex/transcode",
                    "puid": 1313,
                    "pgid": 1313,
                    "devices": devices,
                    "gpu_device": gpu_device,
                },
            },
            "_plex_mounts": [],
            "_plex_advertise_url": "https://plex.example.invalid:443",
            "_plex_claimed": True,
            "_plex_claim_token": "",
            "_plex_media_network": "homelabinfra-gpu-stack",
            "_plex_dri_group_ids": group_ids,
            "homelabinfra_config": {"timezone": "UTC"},
        },
        "tasks": [{
            "ansible.builtin.template": {
                "src": str(compose_template),
                "dest": str(compose_path),
            },
        }],
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
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return compose_path.read_text()


def main():
    server = HTTPServer(("127.0.0.1", 0), PlexPreferences)
    server.requests = []
    server.pending_writes = []
    server.flush_delay = 3
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tempfile.TemporaryDirectory()
    try:
        root = Path(directory.name)
        preferences = root / "Preferences.xml"
        preferences.write_text('<Preferences PlexOnlineToken="fixture-token"/>')
        server.preferences_path = preferences
        playbook = root / "plan.yml"

        changed, _ = run_playbook(server, preferences, playbook, [], expect_retry=True)
        assert changed == 1
        assert server.requests[-1] == {
            "HardwareAcceleratedCodecs": ["0"],
            "HardwareAcceleratedEncoders": ["0"],
        }
        assert ET.parse(preferences).getroot().attrib["HardwareAcceleratedCodecs"] == "0"

        changed, _ = run_playbook(server, preferences, playbook, [])
        assert changed == 0
        assert len(server.requests) == 1

        changed, _ = run_playbook(server, preferences, playbook, ["/dev/dri"], expect_retry=True)
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

        changed, _ = run_playbook(
            server,
            preferences,
            playbook,
            [],
            gpu_device="fixture-igpu",
            expect_retry=True,
        )
        assert changed == 1
        assert server.requests[-1] == {
            "HardwareAcceleratedCodecs": ["1"],
            "HardwareAcceleratedEncoders": ["1"],
        }
        assert ET.parse(preferences).getroot().attrib["HardwareAcceleratedEncoders"] == "1"

        changed, _ = run_playbook(server, preferences, playbook, [], gpu_device="fixture-igpu")
        assert changed == 0
        assert len(server.requests) == 4

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_device_access(root, ["/dev/dri"], "", ["44", "104"])
            run_device_access(root, [], "fixture-igpu", ["44", "104"])
            run_device_access(root, [], "", [])

            gpu_compose = render_compose(root, [], "fixture-igpu", ["44", "104"])
            assert '"/dev/dri:/dev/dri"' in gpu_compose
            assert re.search(r'(?m)^    group_add:\n      - "44"\n      - "104"$', gpu_compose)

            cpu_compose = render_compose(root, [], "", [])
            assert "group_add:" not in cpu_compose
            assert "devices:" not in cpu_compose
    finally:
        for pending in server.pending_writes:
            pending.join()
        directory.cleanup()
        server.shutdown()
        server.server_close()
    print("Plex transcoding: delayed preferences retry to off/on; /dev/dri and gpu_device enable hardware settings and DRI groups; empty devices omit both; repeats are unchanged")


if __name__ == "__main__":
    main()
