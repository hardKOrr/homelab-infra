#!/usr/bin/env python3
"""Exercise Emby's device-driven hardware transcoding settings and convergence."""

import json
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
device_task_file = repo / "ansible/roles/emby/tasks/configure-devices.yml"
transcoding_task_file = repo / "ansible/roles/emby/tasks/configure-transcoding.yml"
compose_template = repo / "ansible/roles/emby/templates/docker-compose.yml.j2"


class EmbyEncoding(BaseHTTPRequestHandler):
    def _authorized(self):
        return self.headers.get("X-Emby-Token") == "fixture-token"

    def do_GET(self):
        if self.path != "/emby/System/Configuration/encoding" or not self._authorized():
            self.send_response(401)
            self.end_headers()
            return

        payload = json.dumps(self.server.encoding).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path != "/emby/System/Configuration/encoding" or not self._authorized():
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        self.server.encoding = json.loads(self.rfile.read(length))
        self.server.requests.append(self.server.encoding)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def run_playbook(server, root, devices, gpu_device=""):
    compose_path = root / "docker-compose.yml"
    playbook_path = root / "configure-emby.yml"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance": "emby",
            "app_config": {
                "app": {
                    "image": "lscr.io/linuxserver/emby:latest",
                    "config_path": "/opt/emby/config",
                    "port": 8097,
                    "container_port": 8096,
                    "puid": 1313,
                    "pgid": 1313,
                    "devices": devices,
                    "gpu_device": gpu_device,
                },
            },
            "_emby_base": f"http://127.0.0.1:{server.server_port}/emby",
            "_emby_token_header": "fixture-token",
            "_emby_mounts": [],
            "homelabinfra_config": {"timezone": "UTC"},
        },
        "tasks": [
            {"ansible.builtin.include_tasks": str(device_task_file)},
            {
                "ansible.builtin.template": {
                    "src": str(compose_template),
                    "dest": str(compose_path),
                },
            },
            {"ansible.builtin.include_tasks": str(transcoding_task_file)},
        ],
    }]
    playbook_path.write_text(yaml.safe_dump(play, sort_keys=False))
    fake_bin = root / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_find = fake_bin / "find"
    fake_find.write_text(
        "#!/bin/sh\n"
        "[ \"${1:-}\" = /dev/dri ] || exit 9\n"
        "printf '104\\n44\\n104\\n'\n"
    )
    fake_find.chmod(0o755)
    env = os.environ | {
        "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    ansible = env.get(
        "GATE_ANSIBLE_PLAYBOOK",
        str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
    )
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
    compose = yaml.safe_load(compose_path.read_text())["services"]["emby"]
    return int(recap.group(1)), compose


def main():
    server = HTTPServer(("127.0.0.1", 0), EmbyEncoding)
    server.encoding = {
        "HardwareAccelerationType": "none",
        "EnableHardwareEncoding": False,
        "HardwareDecodingCodecs": ["h264"],
        "EnableThrottling": True,
    }
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            changed, gpu_service = run_playbook(
                server, root, [], gpu_device="fixture-igpu"
            )
            assert changed == 2  # Compose and Emby's encoding settings changed.
            assert gpu_service["devices"] == ["/dev/dri:/dev/dri"]
            assert gpu_service["group_add"] == ["44", "104"]
            assert server.encoding["HardwareAccelerationType"] == "vaapi"
            assert server.encoding["EnableHardwareEncoding"] is True
            assert server.encoding["HardwareDecodingCodecs"] == ["h264"]
            assert server.encoding["EnableThrottling"] is True
            assert len(server.requests) == 1

            changed, _ = run_playbook(
                server, root, [], gpu_device="fixture-igpu"
            )
            assert changed == 0
            assert len(server.requests) == 1

            changed, cpu_service = run_playbook(server, root, [])
            assert changed == 2  # DRI access and hardware encoding were removed.
            assert "devices" not in cpu_service
            assert "group_add" not in cpu_service
            assert server.encoding["HardwareAccelerationType"] == "none"
            assert server.encoding["EnableHardwareEncoding"] is False
            assert server.encoding["HardwareDecodingCodecs"] == ["h264"]
            assert server.encoding["EnableThrottling"] is True
            assert len(server.requests) == 2

            changed, _ = run_playbook(server, root, [])
            assert changed == 0
            assert len(server.requests) == 2
    finally:
        server.shutdown()
        server.server_close()
    print(
        "Emby transcoding: declared DRI enables VA-API and hardware encoding, passes unique host groups, "
        "and empty device disables hardware; both modes converge on repeat runs"
    )


if __name__ == "__main__":
    main()
