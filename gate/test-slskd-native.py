#!/usr/bin/env python3
"""Guard slskd's native method: pins, rendered unit and environment, shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/slskd"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/slskd.yml").read_text())["slskd_defaults"]

# One exact upstream release for both methods, and a verified release zip.
app = defaults["app"]
assert app["image"] == f"slskd/slskd:{app['native_version']}"
assert len(app["native_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native-install.yml").read_text()
assert "native_sha256" in install and "linux-x64.zip" in install

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "instance": "slskd", "slskd_service": "slskd", "slskd_native_dir": "/opt/slskd/app",
        "slskd_native_env": "/opt/slskd/slskd.env",
        "_slskd_soulseek_username": "user", "_slskd_soulseek_password": 'p;"w d',
        "_slskd_api_key": "fixture-key", "_slskd_web_password": "web-pass",
        "_slskd_complete_dir": "/mnt/data/downloads/complete",
        "_slskd_incomplete_dir": "/mnt/data/downloads/incomplete",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": {"app": {**app, "config_path": "/opt/slskd/config",
                               "shared_dirs": ["/mnt/a", "/mnt/b"], "extra_env": {"SLSKD_X": "1"}}},
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
assert "ExecStart=/opt/slskd/app/slskd" in unit and "EnvironmentFile=/opt/slskd/slskd.env" in unit
assert "fixture-key" not in unit and "web-pass" not in unit
assert "CacheDirectory=slskd" in unit
assert "Environment=DOTNET_BUNDLE_EXTRACT_BASE_DIR=/var/cache/slskd" in unit
lines = set(env.splitlines())
for expected in ('SLSKD_APP_DIR="/opt/slskd/config"', f'SLSKD_HTTP_PORT="{app["port"]}"',
                 'SLSKD_API_KEY="fixture-key"', 'SLSKD_DOWNLOADS_DIR="/mnt/data/downloads/complete"',
                 'SLSKD_SHARED_DIR="/mnt/a;/mnt/b"', 'SLSKD_X="1"', 'SLSKD_SLSK_PASSWORD="p;\\"w d"'):
    assert expected in lines, (expected, env)

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "slskd_native" in upload["vars"]["pbs_client_runtime"]
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_slskd_restore_runtime" in guest
assert "'native'" in (role / "tasks/restore.yml").read_text()

# A restore into a differently named instance swaps the target's directory, not the source's.
hook = (role / "tasks/restore-config.yml").read_text()
assert "tasks/recovery/rebase-target-path.yml" in hook and "restore_rebase_key: config_path" in hook

print("slskd native renders its unit and environment and uses the shared backup and restore")
