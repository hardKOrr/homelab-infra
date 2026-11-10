#!/usr/bin/env python3
"""Guard Unpackerr's native method: exact pins, the rendered environment, the playbook wiring."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/unpackerr"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/unpackerr.yml").read_text())["unpackerr_defaults"]

# One exact upstream release for both methods; the package version is the GoLift build of it.
app = defaults["app"]
assert app["native_version"].startswith(app["image"].rsplit(":v", 1)[1] + "-"), app
assert "latest" not in app["image"]
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])
install = (role / "tasks/native.yml").read_text()
assert "packagecloud.io/golift/pkgs" in install and "unpackerr={{ app_config.app.native_version }}" in install

playbook = (repo / "ansible/playbooks/apps/unpackerr.yml").read_text()
assert "tasks_from: provision" in playbook and "in ['docker', 'native']" in playbook

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    arrs = [{"value": {"app": "sonarr", "host": "http://10.0.0.5:8989", "api_key": "key-a"}},
            {"value": {"app": "sonarr", "host": "http://10.0.0.6:8989", "api_key": "key-b"}},
            {"value": {"app": "radarr", "host": "http://10.0.0.7:7878", "api_key": "key-c"}}]
    variables = {"instance": "unpackerr", "_unpackerr_arrs": arrs,
                 "homelabinfra_config": {"timezone": "UTC"}, "app_config": {"app": {**app, "download_path": "/mnt/downloads"}}}
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [
        {"ansible.builtin.template": {"src": str(role / "templates/native.env.j2"), "dest": str(root / "env")}}]}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = set((root / "env").read_text().splitlines())
for expected in ('UN_SONARR_0_API_KEY="key-a"', 'UN_SONARR_1_URL="http://10.0.0.6:8989"',
                 'UN_RADARR_0_PATHS_0="/mnt/downloads"', 'UN_PARALLEL="1"', 'UN_WEBSERVER_LISTEN_ADDR=""',
                 'UN_QUIET="false"', 'UN_LOG_FILE=""'):
    assert expected in lines, expected

# The environment file overrides the packaged unit's quiet mode and log file only as a drop-in
# (drop-ins load after the unit), and the log helper reads the journal.
assert "service.d/homelabinfra.conf" in install and "EnvironmentFile={{ unpackerr_native_env }}" in install
assert "journalctl -u {{ unpackerr_service }}" in install

print("unpackerr native pins one release and renders its environment")
