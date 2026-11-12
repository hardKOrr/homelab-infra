#!/usr/bin/env python3
"""Guard Maintainerr's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/maintainerr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/maintainerr.yml").read_text())["maintainerr_defaults"]

app = defaults["app"]
assert app["image"] == f"ghcr.io/maintainerr/maintainerr:{app['native_version']}", app
assert len(app["native_node_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "Maintainerr/Maintainerr.git" in install and "native_node_sha256" in install
assert "workspaces focus --all --production" in install and "native_yarn_version" in install

playbook = (repo / "ansible/playbooks/apps/maintainerr.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "maintainerr", "maintainerr_native_root": "/opt/maintainerr/src", "maintainerr_project_dir": "/opt/maintainerr",
                 "maintainerr_native_user": "maintainerr", "homelabinfra_config": {"timezone": "UTC"},
                 "app_config": {"app": {**app, "data_path": "/opt/maintainerr/data"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}},
        {"ansible.builtin.template": {"src": str(role / "templates/native-start.sh.j2"), "dest": str(root / "start")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    start = (root / "start").read_text()
for expected in ("ExecStart=/opt/maintainerr/start.sh", "DATA_DIR=/opt/maintainerr/data",
                 "UI_PORT=6246", "User=maintainerr", "WorkingDirectory=/opt/maintainerr/src/apps/server"):
    assert expected in unit, expected
assert "exec node dist/main" in start and 'served="/opt/maintainerr/data/ui"' in start
print("maintainerr native: ok")
