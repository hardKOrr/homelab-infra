#!/usr/bin/env python3
"""Guard qBittorrent's native method: pins, rendered unit and config, shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/qbittorrent"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/qbittorrent.yml").read_text())["qbittorrent_defaults"]

# One exact upstream release for both methods.
app = defaults["app"]
assert app["image"].split(":")[1].startswith(f"{app['native_version']}-r")
assert app["native_package_version"].startswith(f"{app['native_version']}-")
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/main/native-install.yml").read_text()
assert "native_package_version" in install and "qbittorrent-nox=" in install

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "instance": "qbittorrent", "qbittorrent_service": "qbittorrent", "_qbt_webui_port": 8080,
        "_qbt_password_hash": "fixture-hash", "qbittorrent_peer_outgoing_ports_min": 61000,
        "qbittorrent_peer_outgoing_ports_max": 65535,
        "app_config": {"app": {"port": 8080, "config_path": "/opt/qbittorrent/config",
                               "download_path": "/mnt/data/downloads", "webui_user": "admin"}},
    }
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": [{"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}}
                  for src, dest in [("native.service.j2", "unit"), ("qBittorrent.conf.j2", "conf")]]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    conf = (root / "conf").read_text()
assert "XDG_CONFIG_HOME=/opt/qbittorrent/config" in unit and "XDG_DATA_HOME=/opt/qbittorrent/config" in unit
assert "ExecStart=/usr/bin/qbittorrent-nox --webui-port=8080" in unit
assert "WebUI\\Port=8080" in conf and "WebUI\\Password_PBKDF2=\"fixture-hash\"" in conf

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "qbittorrent_native" in upload["vars"]["pbs_client_runtime"]
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_qbt_restore_runtime" in guest
assert "'native'" in (role / "tasks/restore.yml").read_text()

print("qBittorrent native renders its unit and config and uses the shared backup and restore")
