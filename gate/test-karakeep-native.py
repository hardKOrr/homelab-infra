#!/usr/bin/env python3
"""Guard Karakeep's native method: pins, rendered configuration and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/karakeep"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/karakeep.yml").read_text())["karakeep_defaults"]

# One exact release for both methods, and a verified install script.
app = defaults["app"]
assert app["image"].endswith(":" + app["native_version"])
assert len(app["native_script_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])

native = (role / "tasks/native.yml").read_text()
assert "karakeep-linux.sh" in native and "native_script_sha256" in native
assert "native_meilisearch_version" in native

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    render = []
    for src, dest in [("native.env.j2", "env"), ("meilisearch.toml.j2", "toml")]:
        render.append({"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}})
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"ansible_managed": "# generated", "karakeep_fqdn": "karakeep.example.test",
                 "karakeep_data_path": "/var/lib/karakeep",
                 "karakeep_meilisearch_data_path": "/var/lib/meilisearch/data",
                 "_karakeep_nextauth_secret": "auth-secret", "_karakeep_meili_master_key": "meili-key",
                 "app_config": {"app": {"native_version": app["native_version"], "port": app["port"], "disable_signups": False, "db_wal_mode": True}}},
        "tasks": render}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    env = (root / "env").read_text()
    toml = (root / "toml").read_text()
assert "SERVER_VERSION=0.33.2" in env and "DATA_DIR=/var/lib/karakeep" in env
assert f"PORT={app['port']}" in env and "MEILI_MASTER_KEY=meili-key" in env
assert 'master_key = "meili-key"' in toml and 'db_path = "/var/lib/meilisearch/data"' in toml

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for block in backup for t in ([block] + block.get("block", []))
              if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "app_config.hosting.method" in upload["vars"]["pbs_client_runtime"]
swap = swap_inputs("karakeep", "restore-guest.yml")
assert "app_config.hosting.method" in swap["recovery_swap_runtime"]
dispatch = (role / "tasks/restore.yml").read_text()
assert "restore-guest.yml" in dispatch and "'native'" in dispatch

print("Karakeep native renders its configuration and uses the shared backup and restore")
