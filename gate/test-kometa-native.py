#!/usr/bin/env python3
"""Guard Kometa's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/kometa"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/kometa.yml").read_text())["kometa_defaults"]

app = defaults["app"]
assert app["image"].endswith(f":v{app['native_version']}"), app
assert len(app["native_uv_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "/opt/uv-{{ app_config.app.native_uv_version }}/uv" in install, "uv path must follow the pin"
assert "git" in install and "uv sync --frozen" in install and "native_uv_sha256" in install
assert app["native_python_version"] == "3.13", "match the pinned upstream base image"
assert "--python {{ app_config.app.native_python_version }}" in install
assert "app_config.app.native_version ~ '-python-' ~ app_config.app.native_python_version" in install

playbook = (repo / "ansible/playbooks/apps/kometa.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "kometa", "kometa_native_root": "/opt/kometa/src", "kometa_native_user": "kometa",
                 "homelabinfra_config": {"timezone": "UTC"}, "app_config": {"app": {**app, "config_path": "/opt/kometa/config"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
for expected in ("ExecStart=/opt/kometa/src/.venv/bin/python kometa.py", "KOMETA_CONFIG=/opt/kometa/config/config.yml",
                 "KOMETA_TIMES=05:00", "User=kometa"):
    assert expected in unit, expected
print("kometa native: ok")
