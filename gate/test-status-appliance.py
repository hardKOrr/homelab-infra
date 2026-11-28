#!/usr/bin/env python3
"""Lab Status must not SSH into an ISO appliance, and must still check every other guest."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import jinja2
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
check("a native app is not", not iso_appliance(defaults("ntfy")))
check("an empty config is not", not iso_appliance({}))

plays = yaml.safe_load((ROOT / "ansible/playbooks/maintenance/status.yml").read_text())
play = next(p for p in plays if p["name"] == "Status | Gather guest detail")
gate = [t for t in play["tasks"] if "block" in t]
check("one block holds the SSH checks", len(gate) == 1)

env = jinja2.Environment()
env.tests["iso_appliance"] = iso_appliance
env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")


def enters_block(appliance):
    """Evaluate the block's real `when` for a guest the play decided is (not) an appliance."""
    cond = gate[0]["when"]
    cond = cond if isinstance(cond, str) else " and ".join(cond)
    return env.from_string("{{ " + cond.strip("{} ") + " }}").render(_st_appliance=appliance) == "True"


check("a native guest enters the SSH block", enters_block(False))
check("an appliance does not enter the SSH block", not enters_block(True))
names = {t["name"] for t in gate[0]["block"]}
for need in ("Check whether this guest runs Docker", "List containers",
             "Check whether this guest ships lab-update-check"):
    check(f"{need} is inside the block", need in names)
outside = {t["name"] for t in play["tasks"] if "block" not in t}
check("nothing else reaches the guest", not any("Check" in n or "List" in n for n in outside))
check("ansible.cfg loads the test plugin",
      "test_plugins" in (ROOT / "ansible/ansible.cfg").read_text())

# The play's own decision tasks, run over real inventory hosts. The notes record wins over
# the name, so a guest whose name holds no app slug is still an appliance; a suffixed name
# is matched to its app for guests deployed before the record existed.
NOTE = "<!-- homelab-infra:apps -->\n| instance | kind | url | deployed |\n|---|---|---|---|\n| %s | %s | | 2026-01-01 |\n<!-- /homelab-infra:apps -->"
HOSTS = {
    "edge-firewall": (NOTE % ("edge-firewall", "appliance"), True),
    # an instance named "appliance" is a native deployment; notes outside the markers are not records
    "appliance": (NOTE % ("appliance", "native"), False),
    "operator-note": ("| appliance | hand-written |\n" + NOTE % ("operator-note", "native"), False),
    "edge-docker": (NOTE % ("edge-docker", "docker"), False),
    "proxmox-datacenter-manager-secondary": ("", True),
    "bookstack-2": ("", False),
    "ntfy-2": ("", False),
    "ntfy": ("", False),
}
if shutil.which("ansible-playbook"):
    names_before_block = [t for t in play["tasks"] if "block" not in t and "Check" not in t["name"]]
    tasks = names_before_block[:4]
    tasks.append({"ansible.builtin.debug": {"msg": "{{ inventory_hostname }}={{ _st_appliance | bool }}"}})
    inventory = {"all": {"hosts": {h: {"proxmox_description": d} for h, (d, _) in HOSTS.items()}}}
    with tempfile.TemporaryDirectory() as tmp:
        inv = Path(tmp) / "inv.yml"
        inv.write_text(yaml.safe_dump(inventory))
        probe = ROOT / "ansible/playbooks/maintenance" / f"_probe_{Path(tmp).name}.yml"
        probe.write_text(yaml.safe_dump([{"hosts": "all", "gather_facts": False,
                                          "connection": "local", "tasks": tasks}]))
        try:
            out = subprocess.run(["ansible-playbook", "-i", str(inv), str(probe)],
                                 cwd=ROOT / "ansible", capture_output=True, text=True, check=True).stdout
        finally:
            probe.unlink()
    for host, (_, want) in HOSTS.items():
        check(f"{host} appliance={want}", f"{host}={want}" in out)

# Play 3: a skipped appliance is not UNREACHABLE and is not counted as an SSH guest.
report = next(p for p in plays if p["name"] == "Status | Report")
env.filters["extract"] = lambda name, hv: hv[name]
hv = {
    "pdm": {"inventory_hostname": "pdm", "_st_ssh_skipped": True},
    "web": {"inventory_hostname": "web", "_st_containers": []},
    "down": {"inventory_hostname": "down"},
}
for var, want in (("_st_unreachable", ["down"]), ("_st_ssh_guests", ["web", "down"])):
    got = env.from_string(report["vars"][var]).render(
        _st_guests=["down", "pdm", "web"], hostvars=hv)
    check(f"{var} leaves the appliance out", sorted(eval(got)) == sorted(want))
row = [t for t in report["tasks"] if "_st_report" in str(t)]
check("the guest row marks UNREACHABLE only when SSH was tried",
      "hostvars[h]._st_ssh_skipped is not defined" in str(row))
print("ok")
