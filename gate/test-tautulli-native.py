#!/usr/bin/env python3
"""Guard Tautulli's native method: pins, rendered unit and environment, shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/tautulli"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/tautulli.yml").read_text())["tautulli_defaults"]

# One exact upstream tag for both methods, cloned into /opt/Tautulli.
app = defaults["app"]
assert app["image"] == f"ghcr.io/tautulli/tautulli:{app['native_version']}"
assert app["native_version"].startswith("v") and "latest" not in app["image"]
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native-install.yml").read_text()
assert "https://github.com/Tautulli/Tautulli.git" in install and "native_version" in install
assert "tautulli_native_dir: /opt/Tautulli" in (role / "defaults/main.yml").read_text()

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "instance": "tautulli", "tautulli_service": "tautulli", "tautulli_native_dir": "/opt/Tautulli",
        "tautulli_native_env": "/opt/tautulli/tautulli.env",
        "_tautulli_plex_url": "http://10.0.0.5:32400", "_tautulli_plex_host": "10.0.0.5",
        "_tautulli_plex_runtime_port": 32400, "_tautulli_plex_identifier": "machine-id",
        "_tautulli_plex_token": "plex-token", "_tautulli_api_key": "fixture-key",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": {"app": {**app, "config_path": "/opt/tautulli/config"}},
    }
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": [{"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}}
                  for src, dest in [("native.service.j2", "unit"), ("native.env.j2", "env")]]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    env = (root / "env").read_text()
assert ("ExecStart=/usr/bin/python3 /opt/Tautulli/Tautulli.py --datadir /opt/tautulli/config "
        f"--port {app['port']} --nolaunch") in unit
assert "EnvironmentFile=/opt/tautulli/tautulli.env" in unit
assert "plex-token" not in unit and "fixture-key" not in unit
lines = set(env.splitlines())
for expected in ('TAUTULLI_PMS_TOKEN="plex-token"', 'TAUTULLI_API_KEY="fixture-key"',
                 'TAUTULLI_PMS_URL="http://10.0.0.5:32400"', 'TAUTULLI_PMS_IDENTIFIER="machine-id"'):
    assert expected in lines, (expected, env)

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "tautulli_native" in upload["vars"]["pbs_client_runtime"]
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_tautulli_restore_runtime" in guest
assert "'native'" in (role / "tasks/restore.yml").read_text()
hook = (role / "tasks/restore-config.yml").read_text()
assert "tasks/recovery/rebase-target-path.yml" in hook and "restore_rebase_key: config_path" in hook

print("tautulli native renders its unit and environment and uses the shared backup and restore")
