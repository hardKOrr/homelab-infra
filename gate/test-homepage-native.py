#!/usr/bin/env python3
"""Guard Homepage's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/homepage"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/homepage.yml").read_text())["homepage_defaults"]

app = defaults["app"]
assert app["image"].endswith(f":v{app['native_version']}"), app
for key in ("native_node_sha256", "native_pnpm_sha256"):
    assert len(app[key]) == 64, key
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "git" in install and "--frozen-lockfile" in install and "native_pnpm_sha256" in install

playbook = (repo / "ansible/playbooks/apps/homepage.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "homepage", "homepage_native_root": "/opt/homepage", "homepage_native_config": "/etc/homepage",
                 "homepage_native_user": "homepage", "homepage_fqdn": "homepage.example.test",
                 "homelabinfra_config": {"timezone": "UTC"}, "app_config": {"app": app}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
for expected in ("next start -H 0.0.0.0 -p 3000", "HOMEPAGE_ALLOWED_HOSTS=homepage.example.test",
                 "HOMEPAGE_CONFIG_DIR=/etc/homepage", "User=homepage"):
    assert expected in unit, expected
print("homepage native: ok")
