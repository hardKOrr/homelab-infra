#!/usr/bin/env python3
"""Guard Prowlarr's native method: pins, rendered unit and config, and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/servarr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/prowlarr.yml").read_text())["prowlarr_defaults"]

# One exact build for both methods, and a verified tarball.
app = defaults["app"]
assert app["image"].split(":")[1].startswith(f"{app['native_version']}-ls")
assert len(app["native_sha256"]) == 64 and app["native_branch"] == "master"
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
native = (role / "tasks/native.yml").read_text()
assert "native_sha256" in native and "updatefile?version=" in native

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "ansible_managed": "generated", "_servarr_kind": "prowlarr", "_servarr_prefix": "PROWLARR",
        "servarr_service": "prowlarr", "servarr_native_dir": "/opt/prowlarr/bin",
        "_servarr_api_key": "fixture-key", "instance": "prowlarr",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": {"app": {"port": app["port"], "config_path": "/opt/prowlarr/config"}},
    }
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": [{"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}}
                  for src, dest in [("native.service.j2", "unit"), ("native-config.xml.j2", "config.xml")]]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    config = (root / "config.xml").read_text()
assert "ExecStart=/opt/prowlarr/bin/Prowlarr -nobrowser -data=/opt/prowlarr/config" in unit
assert "PROWLARR__AUTH__METHOD=External" in unit and f"PROWLARR__SERVER__PORT={app['port']}" in unit
# The key lives in config.xml only, so a restored config.xml is never masked by the unit.
assert "APIKEY" not in unit and "<ApiKey>fixture-key</ApiKey>" in config

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "servarr_native" in upload["vars"]["pbs_client_runtime"]
assert "restore-guest.yml" in (role / "tasks/restore.yml").read_text()
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_servarr_restore_runtime" in guest

print("Prowlarr native renders its unit and config and uses the shared backup and restore")
