#!/usr/bin/env python3
"""Guard Frigate's native method: rendered units, config, pins and shared-operation inputs."""

import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/frigate"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/frigate.yml").read_text())["frigate_defaults"]

# One exact version for both methods, and no floating tag.
app = defaults["app"]
assert app["image"].endswith(":" + app["native_version"])
assert len(app["native_sha256"]) == 64
assert all(len(a["sha256"]) == 64 and a["url"].startswith("https://") for a in app["native_assets"].values())
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])


def render():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        tasks = [{"ansible.builtin.template": {
            "src": str(role / "templates/config.yml.j2"), "dest": str(root / "config.yml")}}]
        for unit in ("frigate", "go2rtc", "nginx"):
            tasks.append({"ansible.builtin.template": {
                "src": str(role / "templates/native.service.j2"), "dest": str(root / f"{unit}.service")},
                "vars": {"item": {"name": unit, "after": "", "needs": "x.service" if unit == "frigate" else ""}}})
            tasks.append({"ansible.builtin.template": {
                "src": str(role / f"templates/native-{unit}.sh.j2"), "dest": str(root / f"{unit}.sh")}})
        playbook = root / "render.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"ansible_managed": "# generated", "instance": "frigate",
                     "frigate_project_dir": "/opt/frigate-x", "frigate_native_venv": "/opt/frigate-x/venv",
                     "_frigate_mqtt_password": "",
                     "app_config": {"hosting": {"method": "native"}, "app": {
                         "detector": "cpu", "hwaccel_args": "", "mqtt": {}, "cameras": {}, "port": 8971}}},
            "tasks": tasks}]))
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return {p.name: p.read_text() for p in root.iterdir() if p.name != "render.yml"}


files = render()
config = yaml.safe_load(files["config.yml"])
assert config["networking"]["listen"] == {"internal": "127.0.0.1:5000", "external": 8971}
assert config["detectors"] == {"cpu1": {"type": "cpu"}}
assert "Wants=x.service" in files["frigate.service"]
assert "ExecStart=/opt/frigate-x/frigate.sh" in files["frigate.service"]
assert "exec python3 -u -m frigate" in files["frigate.sh"]
assert "exec /usr/local/nginx/sbin/nginx" in files["nginx.sh"]
assert "go2rtc -config=" in files["go2rtc.sh"]

# Docker keeps its container-port map: no networking override.
docker = subprocess.run(
    ["grep", "-c", "networking", str(role / "templates/docker-compose.yml.j2")], capture_output=True, text=True)
assert docker.stdout.strip() == "0"

# Backup and restore go through the shared operations with the method's runtime.
backup = yaml.safe_load((role / "tasks/backup.yml").read_text())
upload = next(t for t in backup if "pbs-client-backup.yml" in str(t.get("ansible.builtin.include_tasks", "")))
assert upload["vars"]["pbs_client_runtime"] == "{{ app_config.hosting.method }}"
swap = swap_inputs("frigate", "restore-guest.yml")
assert swap["recovery_swap_runtime"] == "{{ app_config.hosting.method }}"
assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
dispatch = (role / "tasks/restore.yml").read_text()
assert "restore-guest.yml" in dispatch and "'native'" in dispatch

# With the default instance name the project and config directories are inside upstream's
# fixed /opt/frigate: build paths must stay outside it, and the install may remove only
# upstream's code there.
role_defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
for name in ("frigate_native_build", "frigate_native_src", "frigate_native_venv", "frigate_native_node"):
    assert not role_defaults[name].startswith("/opt/"), name
assert defaults["app"]["config_path"] == "/opt/{{ instance }}/config"
install = (role / "tasks/native-install.yml").read_text()
removed = [line for line in install.splitlines() if "rm -rf" in line and "/opt/frigate" in line]
assert removed == ["      rm -rf /opt/frigate/frigate /opt/frigate/migrations /opt/frigate/web"], removed
assert "frigate_project_dir" not in install
assert "src-{{ app_config.app.native_version" in (role / "defaults/main.yml").read_text()

print("Frigate native renders its units and config and uses the shared backup and restore")
