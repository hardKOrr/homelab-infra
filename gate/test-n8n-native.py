#!/usr/bin/env python3
"""Guard n8n's native method: pins, the global npm install and the shared-operation inputs."""

from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/n8n"
app = yaml.safe_load((repo / "ansible/vars/app-defaults/n8n.yml").read_text())["n8n_defaults"]["app"]

# One exact n8n release for both methods, and a verified Node.js archive.
assert app["image"].endswith(f":{app['native_version']}")
assert len(app["native_node_sha256"]) == 64
native = (role / "tasks/native.yml").read_text()
assert "[npm, install, -g," in native and "native_version" in native
assert "native_node_sha256" in native

# Backup and restore run through the shared operations with the guest's runtime.
backup = (role / "tasks/backup.yml").read_text()
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in backup
swap = swap_inputs("n8n", "restore-guest.yml")
assert "app_config.hosting.method" in swap["recovery_swap_runtime"]
assert swap["recovery_swap_stop_services"] == ["n8n"]
quiesced = (role / "tasks/backup-quiesced.yml").read_text()
assert "database_client_runtime" in quiesced and "tasks/database/dump.yml" in quiesced

print("n8n native pins one release and reuses the shared backup, restore and dump")
