#!/usr/bin/env python3
"""Guard Mixpost's native method: exact pins, the rendered units and site, the shared operations."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/mixpost"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/mixpost.yml").read_text())["mixpost_defaults"]

app = defaults["app"]
assert app["image"] == f"inovector/mixpost:v{app['native_version']}", app
assert app["native_app_version"].count(".") == 2 and app["native_version"].count(".") == 2
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])

install = (role / "tasks/native.yml").read_text()
assert "create-project" in install and "inovector/MixpostApp:{{ app_config.app.native_app_version }}" in install
assert "inovector/mixpost:{{ app_config.app.native_version }}" in install

# Backup, restore and the database dump use the shared operations with the native runtime.
for name, shared in (("backup.yml", "tasks/backup/pbs-client-backup.yml"),
                     ("backup-quiesced.yml", "tasks/database/dump.yml"),
                     ("restore-guest.yml", "tasks/recovery/restore-swap.yml")):
    text = (role / "tasks" / name).read_text()
    assert shared in text and "'native' if mixpost_native" in text, name

playbook = (repo / "ansible/playbooks/apps/mixpost.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for play in yaml.safe_load(playbook):
    for task in play.get("tasks", []):
        if task["name"] == "Ensure Docker is installed":
            assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "mixpost", "mixpost_php_version": "8.2", "mixpost_env_file": "/opt/mixpost/mixpost.env",
                 "mixpost_native_root": "/opt/mixpost/src", "mixpost_native_user": "www-data",
                 "mixpost_native": True, "_mp_app_key": "base64:x", "_mp_redis_password": "p",
                 "mixpost_fqdn": "mixpost.example.test", "app_config": {"app": {**app, "database": {"name": "mixpost"},
                 "database_host": "db", "database_port": 3306, "database_user": "mixpost", "database_password": "pw"}}}
    tasks = []
    for unit in ("horizon", "scheduler"):
        tasks.append({"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"),
                                                   "dest": str(root / unit)}, "vars": {"_mp_unit": unit}})
    for name in ("nginx-site.conf", "mixpost.env"):
        tasks.append({"ansible.builtin.template": {"src": str(role / f"templates/{name}.j2"), "dest": str(root / name)}})
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": tasks}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    horizon, scheduler, nginx, env = [(root / n).read_text() for n in ("horizon", "scheduler", "nginx-site.conf", "mixpost.env")]
assert "artisan horizon" in horizon and "EnvironmentFile=/opt/mixpost/mixpost.env" in horizon
assert "schedule:run" in scheduler and "User=www-data" in scheduler
assert f"listen {app['port']};" in nginx and "unix:/run/php/php8.2-fpm.sock" in nginx and "root /opt/mixpost/src/public;" in nginx
assert 'REDIS_HOST="127.0.0.1"' in env and 'DB_HOST="db"' in env
print("mixpost native: ok")
