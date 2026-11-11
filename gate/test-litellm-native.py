#!/usr/bin/env python3
"""Guard LiteLLM's native method: exact pins, the rendered unit and env file, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/litellm"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/litellm.yml").read_text())["litellm_defaults"]

app = defaults["app"]
assert app["image"].endswith(f":v{app['native_version']}"), app
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
# prisma lives in extra_proxy, not proxy; the proxy exits without it when DATABASE_URL is set.
assert 'litellm[proxy,extra_proxy]=={{ app_config.app.native_version }}' in install
assert "import prisma" in install

playbook = (repo / "ansible/playbooks/apps/litellm.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] == "Ensure Docker is installed":
        assert "== 'docker'" in task["when"], "native guests do not get Docker"
for name in ("backup.yml", "backup-quiesced.yml", "restore-guest.yml"):
    assert "litellm_native" in (role / "tasks" / name).read_text(), name

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "litellm", "litellm_native": True, "litellm_native_user": "litellm",
                 "litellm_native_venv": "/opt/litellm/venv", "litellm_env_file": "/opt/litellm/litellm.env",
                 "litellm_config_dir": "/opt/litellm/config", "app_config": {"app": app},
                 "_litellm_env": {"LITELLM_MASTER_KEY": 'a"b\\c$d'}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}},
        {"ansible.builtin.template": {"src": str(role / "templates/litellm.env.j2"), "dest": str(root / "env")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    env = (root / "env").read_text()
for expected in ("ExecStart=/opt/litellm/venv/bin/litellm --config /opt/litellm/config/config.yaml --port 4000",
                 "EnvironmentFile=/opt/litellm/litellm.env", "User=litellm"):
    assert expected in unit, expected
assert 'LITELLM_MASTER_KEY="a\\"b\\\\c$d"' in env, env  # systemd does not expand `$`
print("litellm native: ok")
