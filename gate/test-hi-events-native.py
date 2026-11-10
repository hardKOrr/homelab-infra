#!/usr/bin/env python3
"""Guard Hi.Events' native method: exact pins, the rendered units and site, the shared operations."""

import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/hi-events"
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/hi-events.yml").read_text())["hi_events_defaults"]

app = defaults["app"]
assert app["image"].endswith(f":v{app['native_version']}"), app
for key in ("native_sha256", "native_node_sha256", "native_yarn_sha256"):
    assert len(app[key]) == 64, key
assert {"native-lxc", "native-vm"} <= set(defaults["hosting"]["supported"])

build = (role / "tasks/native-build.yml").read_text()
assert "native_sha256" in build and "--frozen-lockfile" in build and "--no-dev" in build

# Backup, restore and the database dump use the shared operations with the native runtime.
for name, shared in (("backup.yml", "tasks/backup/pbs-client-backup.yml"),
                     ("backup-quiesced.yml", "tasks/database/dump.yml"),
                     ("restore-guest.yml", "tasks/recovery/restore-swap.yml")):
    text = (role / "tasks" / name).read_text()
    assert shared in text and "'native' if hi_events_native" in text, name

playbook = (repo / "ansible/playbooks/apps/hi-events.yml").read_text()
assert "tasks_from: provision" in playbook and "app_config.hosting.method == 'native'" in playbook
for play in yaml.safe_load(playbook):
    for task in play.get("tasks", []):
        if task["name"] == "Ensure Docker is installed":
            assert "== 'docker'" in task["when"], "native guests do not get Docker"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    variables = {"instance": "hi-events", "hi_events_php_version": "8.2", "hi_events_env_file": "/opt/hi-events/hi-events.env",
                 "hi_events_native_backend": "/opt/hi-events/src/backend", "hi_events_native_frontend": "/opt/hi-events/src/frontend",
                 "hi_events_native_user": "www-data", "hi_events_data_path": "/opt/hi-events/storage",
                 "app_config": {"app": app}}
    tasks = []
    for unit in ("web", "queue", "scheduler"):
        tasks.append({"ansible.builtin.template": {"src": str(role / "templates/native.service.j2"),
                                                   "dest": str(root / unit)}, "vars": {"_he_unit": unit}})
    tasks.append({"ansible.builtin.template": {"src": str(role / "templates/nginx-site.conf.j2"), "dest": str(root / "nginx")}})
    play = root / "render.yml"
    play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": tasks}]))
    result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    web, queue, scheduler, nginx = [(root / n).read_text() for n in ("web", "queue", "scheduler", "nginx")]
assert "ExecStart=/usr/local/bin/node server.js" in web and "EnvironmentFile=/opt/hi-events/hi-events.env" in web
assert "queue:work --queue=default,webhook-queue" in queue and "schedule:run" in scheduler
assert f"listen {app['port']};" in nginx and "unix:/run/php/php8.2-fpm.sock" in nginx
print("hi-events native: ok")
