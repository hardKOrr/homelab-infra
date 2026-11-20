#!/usr/bin/env python3
"""Remove refuses delete_data on a database backend while another deployed instance's config
names it (app.database.instance or app.redis.instance); a removed app's config does not count."""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
TASK = ROOT / "ansible/tasks/database/guard-backend-consumers.yml"

wiring = yaml.safe_load((ROOT / "ansible/tasks/removal/resolve-instance.yml").read_text())
step = next(t for t in wiring if "guard-backend-consumers.yml" in str(t.get("ansible.builtin.include_tasks")))
assert "_rm_database_backend" in str(step["when"]) and "delete_data" in str(step["when"])


def run(configs: dict, instance: str, tags: str = "_+lab;_authentik;_plane") -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        apps = Path(tmp) / "apps"
        apps.mkdir()
        for name, data in configs.items():
            (apps / f"{name}.yml").write_text(yaml.safe_dump(data))
        play = Path(tmp) / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "connection": "local",
            "vars": {"instance": instance, "_luv_config_dir": tmp,
                     "_rm_backend_guests_json": [{"name": "stack-sso", "tags": tags},
                                                 {"name": "template"}]},
            "tasks": [{"ansible.builtin.include_tasks": str(TASK)}]}]))
        env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible/ansible.cfg"), ANSIBLE_NOCOLOR="1")
        return subprocess.run([str(ANSIBLE), "-i", "localhost,", str(play)], env=env,
                              capture_output=True, text=True, timeout=120)


configs = {"authentik": {"app": {"database": {"provider": "postgresql", "instance": "postgresql-immich"}}},
           "plane": {"app": {"redis": {"instance": "redis-plane"}}},
           "postgresql-immich": {"proxmox": {"guest_type": "vm"}},
           "empty": None}
refused = run(configs, "postgresql-immich")
assert refused.returncode != 0 and "authentik" in refused.stdout, refused.stdout
refused = run(configs, "redis-plane")
assert refused.returncode != 0 and "plane" in refused.stdout, refused.stdout
# A removed app keeps its config, but no guest carries its tag any more.
allowed = run(configs, "postgresql-immich", tags="_+lab;_plane")
assert allowed.returncode == 0, allowed.stdout + allowed.stderr
allowed = run(configs, "postgresql-forgejo")
assert allowed.returncode == 0, allowed.stdout + allowed.stderr
print("remove: a backend a deployed app names is refused; a removed or unrelated one passes")
