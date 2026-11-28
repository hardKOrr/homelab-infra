#!/usr/bin/env python3
"""Lab Status must not SSH into an ISO appliance, and must still check every other guest."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ansible" / "test_plugins"))
from hosting import iso_appliance  # noqa: E402


def defaults(app):
    data = yaml.safe_load((ROOT / "ansible/vars/app-defaults" / f"{app}.yml").read_text())
    return data[app.replace("-", "_") + "_defaults"]


def check(name, condition):
    if not condition:
        raise AssertionError(name)


check("PDM is an appliance", iso_appliance(defaults("proxmox-datacenter-manager")))
check("a Docker app is not", not iso_appliance(defaults("bookstack")))
check("an empty config is not", not iso_appliance({}))

plays = yaml.safe_load((ROOT / "ansible/playbooks/maintenance/status.yml").read_text())
play = next(p for p in plays if p["name"] == "Status | Gather guest detail")
gate = [t for t in play["tasks"] if "block" in t]
check("one block holds the SSH checks", len(gate) == 1)
check("the block skips appliances", "is iso_appliance" in str(gate[0]["when"]))
names = {t["name"] for t in gate[0]["block"]}
for need in ("Check whether this guest runs Docker", "List containers",
             "Check whether this guest ships lab-update-check"):
    check(f"{need} is inside the block", need in names)
outside = {t["name"] for t in play["tasks"] if "block" not in t}
check("nothing else reaches the guest", not any("Check" in n or "List" in n for n in outside))
check("ansible.cfg loads the test plugin",
      "test_plugins" in (ROOT / "ansible/ansible.cfg").read_text())

# A suffixed instance resolves to the longest app slug, then is classified by it.
if shutil.which("ansible-playbook"):
    tasks = [t for t in play["tasks"] if "block" not in t][:3]
    tasks.append({"ansible.builtin.debug": {"msg": "{{ inventory_hostname }}={{ _st_app_config is iso_appliance }}"}})
    with tempfile.TemporaryDirectory() as tmp:
        probe = ROOT / "ansible/playbooks/maintenance" / f"_probe_{Path(tmp).name}.yml"
        probe.write_text(yaml.safe_dump([{"hosts": "all", "gather_facts": False,
                                          "connection": "local", "tasks": tasks}]))
        try:
            out = subprocess.run(
                ["ansible-playbook", "-i", "proxmox-datacenter-manager-secondary,bookstack-2,", str(probe)],
                cwd=ROOT / "ansible", capture_output=True, text=True, check=True).stdout
        finally:
            probe.unlink()
    check("suffixed appliance is skipped", "proxmox-datacenter-manager-secondary=True" in out)
    check("suffixed native guest is not", "bookstack-2=False" in out)
print("ok")
