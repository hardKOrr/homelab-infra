#!/usr/bin/env python3
"""PBS's Docker method renders one container with the datastore at its configured path, runs
proxmox-backup-manager through docker exec, and the native default is unchanged."""
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(ROOT / "ansible/roles/pbs/templates"),
    undefined=jinja2.StrictUndefined, extensions=["jinja2.ext.do"],
)
env.filters["comment"] = lambda text: "# " + str(text)
env.filters["bool"] = bool
IMAGE = "ayufan/proxmox-backup-server:v4.2.0-1"
text = env.get_template("docker-compose.yml.j2").render(
    ansible_managed="managed", instance="pbs", app_config={"app": {"image": IMAGE, "port": 8007}}, pbs_project_dir="/opt/pbs", _pbs_published=True,
    homelabinfra_config={"infrastructure": {"backups": {"datastore_path": "/mnt/backup"}}},
)
service = yaml.safe_load(text)["services"]["pbs"]
assert service["image"] == IMAGE and service["stop_signal"] == "SIGHUP", service
closed = yaml.safe_load(env.get_template("docker-compose.yml.j2").render(
    ansible_managed="m", instance="pbs", app_config={"app": {"image": IMAGE, "port": 8007}},
    homelabinfra_config={"infrastructure": {"backups": {"datastore_path": "/mnt/backup"}}},
    pbs_project_dir="/opt/pbs", _pbs_published=False))["services"]["pbs"]
assert closed["ports"] == ["127.0.0.1:8007:8007"], closed
assert service["tmpfs"] == ["/run"] and service["ports"] == ["8007:8007"], service
assert "/mnt/backup:/mnt/backup" in service["volumes"], service
assert "/opt/pbs/etc:/etc/proxmox-backup" in service["volumes"]

defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/pbs.yml").read_text())["pbs_defaults"]
assert defaults["hosting"]["method"] == "native" and defaults["stack"] == ""
assert set(defaults["hosting"]["supported"]) == {"native-vm", "docker-lxc", "docker-vm"}
role = yaml.safe_load((ROOT / "ansible/roles/pbs/defaults/main.yml").read_text())
assert "docker', 'exec'" in role["pbs_manager_argv"] and role["pbs_auth_id"] == "root@pam"
print("PBS Docker method renders as expected.")
sys.exit(0)
