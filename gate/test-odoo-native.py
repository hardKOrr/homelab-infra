#!/usr/bin/env python3
"""Guard Odoo's native method: the pinned 18.0 repository and the shared-operation inputs."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
role = repo / "ansible/roles/odoo"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/odoo.yml").read_text())["odoo_defaults"]

assert "native-lxc" in defaults["hosting"]["supported"] and "native-vm" in defaults["hosting"]["supported"]
assert defaults["hosting"]["method"] == "docker"
app = defaults["app"]
assert "/18.0/" in app["native_repository"] and app["image"].endswith(":18.0")

backup = (role / "tasks/backup.yml").read_text()
assert "tasks/backup/pbs-client-backup.yml" in backup
assert "pbs_client_runtime: \"{{ app_config.hosting.method" in backup
assert "database_client_runtime" in (role / "tasks/backup-quiesced.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in (role / "tasks/restore-guest.yml").read_text()

# A non-default app.port reaches the native listener (Docker keeps 8069 inside the container).
conf = (role / "templates/odoo.conf.j2").read_text()
assert "{% if odoo_native | bool %}" in conf and "http_port = {{ app_config.app.port }}" in conf

print("Odoo native pins the 18.0 repository and reuses the shared backup and restore")
