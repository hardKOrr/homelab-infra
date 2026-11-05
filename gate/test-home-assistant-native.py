#!/usr/bin/env python3
"""Guard Home Assistant's native method: rendered unit, pins and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/home-assistant"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/home-assistant.yml").read_text())["home_assistant_defaults"]

# One exact version for both methods, and no floating tag.
app = defaults["app"]
assert app["image"].endswith(":" + app["native_version"])
assert len(app["native_sha256"]) == 64 and len(app["native_python"]["sha256"]) == 64
assert app["native_python"]["url"].startswith("https://")

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"ansible_managed": "# generated", "instance": "home-assistant",
                 "home_assistant_native_venv": "/opt/ha/native/venv-x",
                 "homelabinfra_config": {"timezone": "UTC"},
                 "app_config": {"hosting": {"method": "native"}, "app": {"config_path": "/opt/ha/config"}}},
        "tasks": [{"ansible.builtin.template": {
            "src": str(role / "templates/native.service.j2"), "dest": str(root / "ha.service")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "ha.service").read_text()
assert "ExecStart=/opt/ha/native/venv-x/bin/hass --config /opt/ha/config" in unit

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert upload["vars"]["pbs_client_runtime"] == "{{ app_config.hosting.method }}"
swap = swap_inputs("home-assistant", "restore-guest.yml")
assert swap["recovery_swap_runtime"] == "{{ app_config.hosting.method }}"
dispatch = (role / "tasks/restore.yml").read_text()
assert "restore-guest.yml" in dispatch and "'native'" in dispatch

print("Home Assistant native renders its unit and uses the shared backup and restore")
