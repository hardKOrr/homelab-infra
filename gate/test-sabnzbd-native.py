#!/usr/bin/env python3
"""Guard SABnzbd's native method: pins, rendered unit and config, shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/sabnzbd"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/sabnzbd.yml").read_text())["sabnzbd_defaults"]

# One exact upstream release for both methods, and a verified tarball.
app = defaults["app"]
assert app["image"].split(":")[1].startswith(f"{app['native_version']}-ls")
assert len(app["native_sha256"]) == 64
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/main/native-install.yml").read_text()
# SABnzbd 5.1.3 sets NO_DOWNLOADING without par2 and unrar; unrar is Debian non-free.
assert "unrar" in install and "par2" in install and "components: [non-free]" in install
# A new source needs its own index refresh before unrar has a candidate.
tasks = yaml.safe_load(install)
repo_at = next(i for i, t in enumerate(tasks) if "ansible.builtin.deb822_repository" in t)
refresh = tasks[repo_at + 1]["ansible.builtin.apt"]
assert refresh["update_cache"] is True and "cache_valid_time" not in refresh
assert "is changed" in tasks[repo_at + 1]["when"]
assert "unrar" in tasks[repo_at + 2]["ansible.builtin.apt"]["name"]
assert "native_sha256" in install and "-src.tar.gz" in install and "requirements.txt" in install

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "instance": "sabnzbd", "sabnzbd_service": "sabnzbd", "sabnzbd_native_dir": "/opt/sabnzbd/app",
        "sabnzbd_native_venv": "/opt/sabnzbd/venv", "_sab_ini": "/opt/sabnzbd/config/sabnzbd.ini",
        "_sab_listen_port": app["port"], "_sab_api_key": "fixture-key",
        "_sab_host_whitelist": ["sabnzbd.example.com"], "_sab_incomplete_dir": "/mnt/data/downloads/incomplete",
        "_sab_complete_dir": "/mnt/data/downloads/complete",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": {"app": {"port": app["port"], "config_path": "/opt/sabnzbd/config"}},
    }
    playbook = root / "render.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False, "vars": variables,
        "tasks": [{"ansible.builtin.template": {"src": str(role / "templates" / src), "dest": str(root / dest)}}
                  for src, dest in [("native.service.j2", "unit"), ("sabnzbd.ini.j2", "ini")]]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    unit = (root / "unit").read_text()
    ini = (root / "ini").read_text()
assert ("ExecStart=/opt/sabnzbd/venv/bin/python /opt/sabnzbd/app/SABnzbd.py "
        "-f /opt/sabnzbd/config/sabnzbd.ini -b 0") in unit
# Natively the app binds app.port itself; the key lives in the seeded ini only.
assert f"port = {app['port']}" in ini and "api_key = fixture-key" in ini and "fixture-key" not in unit

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "sabnzbd_native" in upload["vars"]["pbs_client_runtime"]
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_sabnzbd_restore_runtime" in guest
assert "'native'" in (role / "tasks/restore.yml").read_text()

print("SABnzbd native renders its unit and config and uses the shared backup and restore")
