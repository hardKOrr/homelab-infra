#!/usr/bin/env python3
"""Guard FlareSolverr's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/flaresolverr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/flaresolverr.yml").read_text())["flaresolverr_defaults"]

# One exact upstream release for every method.
app = defaults["app"]
assert app["image"] == f"flaresolverr/flaresolverr:v{app['native_version']}", app
assert app["native_version"].count(".") == 2 and len(app["native_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "flaresolverr_linux_x64.tar.gz" in install and "native_sha256" in install

playbook = (repo / "ansible/playbooks/apps/flaresolverr.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "flaresolverr", "flaresolverr_service": "flaresolverr",
                 "flaresolverr_native_dir": "/opt/flaresolverr-3.5.2", "flaresolverr_home": "/var/lib/flaresolverr",
                 "homelabinfra_config": {"timezone": "UTC"}, "app_config": {"app": app}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
assert "ExecStart=/opt/flaresolverr-3.5.2/flaresolverr/flaresolverr" in unit
for expected in (f'Environment="PORT={app["port"]}"', f'Environment="LOG_LEVEL={app["log_level"]}"', "User=flaresolverr"):
    assert expected in unit, expected
print("flaresolverr native: ok")
