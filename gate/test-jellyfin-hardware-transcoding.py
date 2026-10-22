#!/usr/bin/env python3
"""Exercise Jellyfin's device-driven hardware transcoding settings and convergence."""

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
device_task_file = repo / "ansible/roles/jellyfin/tasks/configure-devices.yml"
transcoding_task_file = repo / "ansible/roles/jellyfin/tasks/configure-transcoding.yml"
compose_template = repo / "ansible/roles/jellyfin/templates/docker-compose.yml.j2"


class JellyfinEncoding(BaseHTTPRequestHandler):
    def _authorized(self):
        return self.headers.get("Authorization") == 'MediaBrowser Token="fixture-token"'

    def do_GET(self):
        if self.path != "/System/Configuration/encoding" or not self._authorized():
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
        if self.path != "/System/Configuration/encoding" or not self._authorized():
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.server.requests.append(payload)
        self.server.encoding = payload
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def run_playbook(server, root, devices, gpu_device=""):
    playbook_path = root / "configure-transcoding.yml"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "app_config": {"app": {"devices": devices, "gpu_device": gpu_device}},
            "_jf_base": f"http://127.0.0.1:{server.server_port}",
            "_jf_auth_header": 'MediaBrowser Token="fixture-token"',
        },
        "tasks": [
            {"ansible.builtin.include_tasks": str(device_task_file)},
            {"ansible.builtin.include_tasks": str(transcoding_task_file)},
        ],
    }]
    playbook_path.write_text(yaml.safe_dump(play, sort_keys=False))
    fake_bin = root / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fake_find = fake_bin / "find"
    fake_find.write_text(
        "#!/bin/sh\n"
        "[ \"${7:-}\" = '%G\\n' ] || exit 9\n"
        "printf '44\\n104\\n104\\n'\n"
    )
    fake_find.chmod(0o755)
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
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "fixture-token" not in output, output
    recap = re.search(r"localhost\s*:\s*ok=\d+\s+changed=(\d+)", output)
    assert recap, output
    return int(recap.group(1)), output


def render_compose(root, devices, gpu_device, group_ids):
    compose_path = root / "docker-compose.yml"
    playbook_path = root / "render-compose.yml"
    dri_declared = bool(gpu_device) or any(device.startswith("/dev/dri") for device in devices)
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance": "jellyfin",
            "app_config": {
                "app": {
                    "image": "lscr.io/linuxserver/jellyfin:latest",
                    "port": 8096,
                    "container_port": 8096,
                    "config_path": "/opt/jellyfin/config",
                    "cache_path": "/opt/jellyfin/cache",
                    "puid": 1313,
                    "pgid": 1313,
                    "devices": devices,
                    "gpu_device": gpu_device,
                },
            },
            "_jf_published_url": "",
            "_jf_mounts": [],
            "_jf_dri_device_declared": dri_declared,
            "_jf_dri_group_ids": group_ids,
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
    return yaml.safe_load(compose_path.read_text())["services"]["jellyfin"]


def main():
    server = HTTPServer(("127.0.0.1", 0), JellyfinEncoding)
    server.encoding = {
        "HardwareDecodingCodecs": ["h264"],
        "TranscodingTempPath": "/cache/transcodes",
    }
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            changed, _ = run_playbook(server, root, [], gpu_device="fixture-igpu")
            assert changed == 1  # Jellyfin's encoding settings were saved.
            assert server.encoding["HardwareAccelerationType"] == "vaapi"
            assert server.encoding["EnableHardwareEncoding"] is True
            assert server.encoding["HardwareDecodingCodecs"] == ["h264", "hevc"]
            assert server.encoding["TranscodingTempPath"] == "/cache/transcodes"
            assert len(server.requests) == 1

            changed, _ = run_playbook(server, root, [], gpu_device="fixture-igpu")
            assert changed == 0
            assert len(server.requests) == 1

            changed, _ = run_playbook(server, root, [])
            assert changed == 1
            assert server.encoding["HardwareAccelerationType"] == "none"
            assert server.encoding["EnableHardwareEncoding"] is False
            assert server.encoding["HardwareDecodingCodecs"] == ["h264", "hevc"]
            assert len(server.requests) == 2

            changed, _ = run_playbook(server, root, [])
            assert changed == 0
            assert len(server.requests) == 2

            gpu_service = render_compose(root, [], "fixture-igpu", ["44", "104"])
            assert "/dev/dri:/dev/dri" in gpu_service["devices"]
            assert gpu_service["group_add"] == ["44", "104"]

            dri_service = render_compose(root, ["/dev/dri"], "", ["44", "104"])
            assert "/dev/dri:/dev/dri" in dri_service["devices"]
            assert dri_service["group_add"] == ["44", "104"]

            cpu_service = render_compose(root, [], "", [])
            assert "devices" not in cpu_service
            assert "group_add" not in cpu_service
    finally:
        server.shutdown()
        server.server_close()
    print("Jellyfin transcoding: declared DRI enables VA-API and HEVC decoding; empty device disables hardware encoding; repeats are unchanged; container receives DRI groups")


if __name__ == "__main__":
    main()
