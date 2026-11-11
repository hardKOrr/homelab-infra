#!/usr/bin/env python3
"""Guard Jellyseerr's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/jellyseerr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/jellyseerr.yml").read_text())["jellyseerr_defaults"]

app = defaults["app"]
assert app["image"].endswith(f"/seerr-team/seerr:v{app['native_version']}"), app
for key in ("native_node_sha256", "native_pnpm_sha256"):
    assert len(app[key]) == 64, key
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "seerr-team/seerr.git" in install and "--frozen-lockfile" in install and "native_pnpm_sha256" in install

playbook = (repo / "ansible/playbooks/apps/jellyseerr.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "jellyseerr", "jellyseerr_native_root": "/opt/jellyseerr/src",
                 "jellyseerr_native_user": "jellyseerr", "homelabinfra_config": {"timezone": "UTC"},
                 "app_config": {"app": app}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
for expected in ("ExecStart=/usr/local/bin/node dist/index.js", "CONFIG_DIRECTORY=/opt/jellyseerr/config",
                 "PORT=5055", "User=jellyseerr", "WorkingDirectory=/opt/jellyseerr/src"):
    assert expected in unit, expected
print("jellyseerr native: ok")
