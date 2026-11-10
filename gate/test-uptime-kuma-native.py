#!/usr/bin/env python3
"""Guard Uptime Kuma's native method: exact pins, the rendered unit, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/uptime-kuma"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/uptime-kuma.yml").read_text())["uptime_kuma_defaults"]

# One exact upstream release for both methods; Node comes from a pinned runtime.
app = defaults["app"]
assert app["image_tag"] == app["native_version"] and app["image_tag"].count(".") == 2, app
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native-install.yml").read_text()
assert "https://github.com/louislam/uptime-kuma.git" in install and "argv: [npm, run, setup]" in install
assert "native_version" in install and "node_version" in install

playbook = (repo / "ansible/playbooks/apps/uptime-kuma.yml").read_text()
assert "tasks_from: provision" in playbook and "in ['docker', 'native']" in playbook
# A native guest gets the same OS baseline as a Docker stack host (bootstrap marker kept).
for task in yaml.safe_load(playbook)[1]["tasks"]:
    if task["name"] in ("Bootstrap guest", "Configure OS update notifications"):
        assert "hosting.method in ['docker', 'native']" in str(task["when"]), task["name"]

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {"instance": "uptime-kuma", "uptime_kuma_service": "uptime-kuma",
                 "uptime_kuma_native_dir": "/opt/uptime-kuma", "uptime_kuma_node_dir": "/opt/node-v24-linux-x64",
                 "homelabinfra_config": {"timezone": "UTC"},
                 "app_config": {"app": {**app, "data_path": "/opt/uptime-kuma/data"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"), "dest": str(root / "unit")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
assert "ExecStart=/opt/node-v24-linux-x64/bin/node /opt/uptime-kuma/server/server.js" in unit
for expected in ('Environment="DATA_DIR=/opt/uptime-kuma/data/"', f'Environment="UPTIME_KUMA_PORT={app["port"]}"',
                 "User=uptime-kuma"):
    assert expected in unit, expected

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "uptime_kuma_native" in upload["vars"]["pbs_client_runtime"]
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_kuma_restore_runtime" in guest
assert "'native'" in (role / "tasks/restore.yml").read_text()

print("uptime-kuma native pins one release, renders its unit and uses the shared backup and restore")
