#!/usr/bin/env python3
"""Guard Sonarr's native method: pins, rendered unit and config, and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/servarr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/sonarr.yml").read_text())["sonarr_defaults"]

# One exact build for both methods, and a verified tarball.
app = defaults["app"]
assert app["image"].split(":")[1].startswith(f"{app['native_version']}-ls")
assert len(app["native_sha256"]) == 64
assert app["native_url"] == "https://api.github.com/repos/Sonarr/Sonarr/releases/assets/568369830"
assert app["native_headers"] == {"Accept": "application/octet-stream"}
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
native_text = (role / "tasks/native.yml").read_text()
native = native_text
assert "native_sha256" in native and "native_url" in native

# The download URL expression from native.yml renders with Sonarr's real defaults (a native_url,
# no native_branch) and still falls back to the update service for a branch-only app.
def find_url(tasks):
    for task in tasks:
        for key in ("block", "rescue"):
            hit = find_url(task.get(key) or [])
            if hit:
                return hit
        if "ansible.builtin.get_url" in task:
            return task["ansible.builtin.get_url"]["url"]
get_url = find_url(yaml.safe_load(native_text))
assert 'headers: "{{ app_config.app.native_headers | default(omit) }}"' in native_text
with tempfile.TemporaryDirectory() as directory:
    play = Path(directory) / "url.yml"
    cases = {"sonarr": app, "other": {"native_version": "1.2.3", "native_branch": "nightly"}}
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": [
        {"ansible.builtin.assert": {"that": [f"({get_url.strip().removeprefix("{{").removesuffix("}}")}) == {expected!r}"]},
         "vars": {"app_config": {"app": values}, "servarr_service": name, "instance": name}}
        for name, values, expected in [
            ("sonarr", app, app["native_url"]),
            ("other", cases["other"], "https://other.servarr.com/v1/update/nightly/updatefile?version=1.2.3&os=linux&runtime=netcore&arch=x64")]]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    variables = {
        "ansible_managed": "generated", "_servarr_kind": "sonarr", "_servarr_prefix": "SONARR",
        "servarr_service": "sonarr", "servarr_native_dir": "/opt/sonarr/bin",
        "_servarr_api_key": "fixture-key", "instance": "sonarr",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": {"app": {"port": app["port"], "config_path": "/opt/sonarr/config"}},
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
assert "ExecStart=/opt/sonarr/bin/Sonarr -nobrowser -data=/opt/sonarr/config" in unit
assert "SONARR__AUTH__METHOD=External" in unit and f"SONARR__SERVER__PORT={app['port']}" in unit
# The key lives in config.xml only, so a restored config.xml is never masked by the unit.
assert "APIKEY" not in unit and "<ApiKey>fixture-key</ApiKey>" in config

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert "servarr_native" in upload["vars"]["pbs_client_runtime"]
assert "restore-guest.yml" in (role / "tasks/restore.yml").read_text()
guest = (role / "tasks/restore-guest.yml").read_text()
assert "tasks/recovery/restore-swap.yml" in guest and "_servarr_restore_runtime" in guest

print("Sonarr native renders its unit and config and uses the shared backup and restore")
