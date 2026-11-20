#!/usr/bin/env python3
"""Check deploy.yml's guest firewall step: when it opens a converted app's access."""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")


def render(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    # Converted apps open the guest's access through deploy.yml's firewall step, before the
    # guest deploy unless firewall.after_deploy defers it (qBittorrent's peer source ports).
    step = yaml.safe_load(read("ansible/tasks/apps/deploy-firewall.yml"))[0]
    assert step["ansible.builtin.include_tasks"] == "../firewall/open-app-access.yml"
    phase = next(c for c in step["when"] if "after_deploy" in c)
    for after, run_phase, ok in ((False, "before", True), (False, "after", False),
                                 (True, "before", False), (True, "after", True)):
        result = render("{{ " + phase + " }}", {"app_config": {"firewall": {"after_deploy": after}},
                                               "deploy_firewall_phase": run_phase})
        assert result is ok, (after, run_phase, result)
    deploy = yaml.safe_load(read("ansible/playbooks/apps/deploy.yml"))
    order = [(play["name"], t.get("vars", {}).get("deploy_firewall_phase"))
             for play in deploy for t in play.get("tasks", []) if "deploy-firewall.yml" in str(t)]
    assert [p for _, p in order] == ["before", "after"], order
    assert order[0][0].endswith("| Resolve and provision") and order[1][0].endswith("| Record and wire")
    qbt = yaml.safe_load(read("ansible/vars/app-defaults/qbittorrent.yml"))["qbittorrent_defaults"]
    assert qbt["firewall"]["after_deploy"] is True
    for app in ("slskd", "sabnzbd"):
        defaults = yaml.safe_load(read(f"ansible/vars/app-defaults/{app}.yml"))[f"{app}_defaults"]
        assert not defaults.get("firewall", {}).get("after_deploy"), app
    hook = yaml.safe_load(read("ansible/roles/qbittorrent/tasks/deploy-prepare.yml"))[0]
    rendered = render(hook["ansible.builtin.set_fact"]["firewall_inbound_ports"]["bittorrent-listen"],
                      {"app_config": qbt, "instance": "qbittorrent"})
    declared = {f["name"]: f["port"] for f in qbt["firewall"]["inbound"]}
    assert str(rendered) == str(qbt["app"]["torrenting_port"]) == str(declared["bittorrent-listen"])

    print("deploy.yml guest firewall step: phases and qBittorrent's deferred peer rules passed")


if __name__ == "__main__":
    main()
