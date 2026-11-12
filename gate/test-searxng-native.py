#!/usr/bin/env python3
"""Guard SearXNG's native method: exact pins, the rendered unit and settings, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/searxng"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/searxng.yml").read_text())["searxng_defaults"]

# One exact upstream release for every method: the image tag's suffix is the pinned commit.
app = defaults["app"]
assert app["image"] == f"searxng/searxng:{app['native_version']}-{app['native_commit'][:9]}", app
assert len(app["native_commit"]) == 40 and app["native_version"].count(".") == 2
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "ansible.builtin.git:" in install and "native_commit" in install and "searxng_venv" in install

playbook = (repo / "ansible/playbooks/apps/searxng.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "searxng", "searxng_service": "searxng", "searxng_src": "/usr/local/searxng/searxng-src",
                 "searxng_venv": "/usr/local/searxng/searx-pyenv", "searxng_settings_path": "/etc/searxng/settings.yml",
                 "searxng_fqdn": "searxng.example.test", "_sx_secret_key": "k", "_sx_redis_password": "r",
                 "homelabinfra_config": {"timezone": "UTC"},
                 "app_config": {"app": {**app, "instance_name": "SearXNG"}, "hosting": {"method": "native"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / f"templates/{name}.j2"), "dest": str(root / name)}}
        for name in ("native.service", "settings.yml")]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "native.service").read_text()
    settings = yaml.safe_load((root / "settings.yml").read_text())
assert f"--port {app['port']} searx.webapp:app" in unit and "User=searxng" in unit
assert settings["server"]["limiter"] is False and "valkey" not in settings, settings
print("searxng native: ok")
