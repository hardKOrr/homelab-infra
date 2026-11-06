#!/usr/bin/env python3
"""Guard Mautic's native method: pins, rendered services and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/mautic"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/mautic.yml").read_text())["mautic_defaults"]

# One exact release for both methods, and a verified package.
app = defaults["app"]
assert app["image"].startswith(f"docker.io/mautic/mautic:{app['native_version']}-")
assert len(app["native_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
native = (role / "tasks/native.yml").read_text() + (role / "tasks/native-services.yml").read_text()
assert "native_sha256" in native and "releases/download/" in native

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    render = []
    for src, dest in [("mautic-apache.conf.j2", "site"), ("mautic-worker.service.j2", "worker"),
                      ("mautic-cron.service.j2", "cron"), ("mautic-cron.sh.j2", "cron.sh")]:
        render.append({"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}})
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "vars": {"ansible_managed": "generated", "instance": "mautic", "mautic_native_root": "/var/www/mautic",
                 "app_config": {"app": {"port": app["port"]}}},
        "tasks": render}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    site, worker, cron, script = [(root / name).read_text() for name in ("site", "worker", "cron", "cron.sh")]
assert f"<VirtualHost *:{app['port']}>" in site and "DocumentRoot /var/www/mautic" in site
assert "AllowOverride All" in site and "FollowSymLinks" in site
assert "messenger:consume email hit failed" in worker and "User=www-data" in worker
assert "ExecStart=/usr/local/bin/mautic-cron" in cron
assert "mautic:segments:update" in script and "mautic:campaigns:trigger" in script
assert script.startswith("#!/bin/sh")

# Backup and restore go through the shared operations with the method's runtime, and the
# cron and worker run apart from the web process.
services = yaml.safe_load((role / "defaults/main.yml").read_text())["mautic_native_services"]
assert services == ["mautic-cron", "mautic-worker", "apache2"]
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for block in backup for t in ([block] + block.get("block", []))
              if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "app_config.hosting.method" in upload["vars"]["pbs_client_runtime"]
swap = swap_inputs("mautic", "restore-guest.yml")
assert "app_config.hosting.method" in swap["recovery_swap_runtime"]
dispatch = (role / "tasks/restore.yml").read_text()
assert "restore-guest.yml" in dispatch and "'native'" in dispatch

print("Mautic native renders its services and uses the shared backup and restore")
