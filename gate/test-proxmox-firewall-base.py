#!/usr/bin/env python3
"""Render and compare the documented Proxmox host-firewall baseline."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "ansible/scripts/proxmox-firewall-plan.py"
PYTHON = Path.home() / ".venvs/homelab-ansible/bin/python"
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
FAILURES = []


def check(name, actual, expected):
    if actual != expected:
        FAILURES.append(f"{name}: expected {expected!r}, got {actual!r}")


def run_plan(payload, *, expect_success=True):
    result = subprocess.run(
        [str(PYTHON), str(PLANNER)],
        input=json.dumps(payload), capture_output=True, text=True, check=False,
    )
    if (result.returncode == 0) != expect_success:
        FAILURES.append(
            f"planner returned {result.returncode}: {result.stdout[-2000:]} {result.stderr[-2000:]}"
        )
    return json.loads(result.stdout) if result.returncode == 0 else {}


def doctor(proxmox, invalid=False):
    with tempfile.TemporaryDirectory() as directory:
        config_dir = Path(directory)
        fixture = yaml.safe_load(
            (ROOT / "gate/fixtures/config-loading/valid/proxmox.yml").read_text())
        fixture["proxmox"].update(copy.deepcopy(proxmox))
        (config_dir / "proxmox.yml").write_text(yaml.safe_dump(fixture))
        (config_dir / "infrastructure.yml").write_text(
            (ROOT / "gate/fixtures/config-loading/valid/infrastructure.yml").read_text())
        result = subprocess.run(
            ["bash", str(ROOT / "ansible/scripts/config-doctor.sh"), str(config_dir)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg")},
        )
        if (result.returncode != 0) != invalid:
            FAILURES.append(
                f"config doctor returned {result.returncode}:\n"
                f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
            )
        return result.stdout + result.stderr


def disabled_playbook_noop():
    playbook = ROOT / "ansible/playbooks/maintenance/reconcile-proxmox-host-firewall.yml"
    with tempfile.TemporaryDirectory() as directory:
        variables = Path(directory) / "vars.yml"
        variables.write_text(yaml.safe_dump({
            "homelabinfra_config": {"proxmox": {}}
        }))
        result = subprocess.run(
            [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(playbook),
             "-e", f"user_vars_file={variables}"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg")},
        )
        check("disabled baseline playbook exits successfully", result.returncode, 0)
        check("disabled baseline reports no changed tasks", "changed=0" in result.stdout, True)


def main():
    example = yaml.safe_load((ROOT / "config.example/proxmox.yml").read_text())
    proxmox = copy.deepcopy(example["proxmox"])
    check("firewall is disabled in the example", proxmox["firewall"]["enabled"], False)
    defaults = yaml.safe_load((ROOT / "ansible/vars/homelabinfra-defaults.yml").read_text())
    check("firewall defaults are disabled", defaults["homelabinfra_defaults"]["proxmox"]["firewall"]["enabled"], False)
    check("management sources are an explicit list",
          proxmox["firewall"]["management_sources"], [])
    check("storage clients are an explicit list",
          proxmox["firewall"]["storage_clients"], [])
    check("storage nodes are documented at proxmox level",
          "storage_nodes" in (ROOT / "config.example/proxmox.yml").read_text(), True)

    proxmox.update({
        "node": "pve-1",
        "api_host": "192.0.2.11",
        "nodes": {"pve-1": "192.0.2.11", "pve-2": "192.0.2.12"},
        "storage_nodes": ["pve-1"],
        "firewall": {
            "enabled": True,
            "management_sources": ["198.51.100.0/24"],
            "storage_clients": ["203.0.113.22/32"],
        },
    })
    status = [
        {"type": "node", "name": "pve-1", "online": 1},
        {"type": "node", "name": "pve-2", "online": 1},
    ]
    empty = {
        "proxmox": proxmox,
        "cluster_status": status,
        "cluster_options": {"enable": 0},
        "cluster_rules": [],
        "node_options": {},
        "node_rules": {},
    }
    first = run_plan(empty)
    check("first reconciliation requires changes", first["changed"], True)
    check("management renders SSH and API rules", {
        (rule.get("source"), rule.get("dport"))
        for rule in first["cluster"]["desired"] if rule.get("proto") == "tcp"
    }, {("198.51.100.0/24", "22"), ("198.51.100.0/24", "8006")})
    check("cluster rules connect each direction between the configured peers", {
        (rule.get("source"), rule.get("dest"))
        for rule in first["cluster"]["desired"] if rule.get("source") in {
            "192.0.2.11", "192.0.2.12"}
    }, {("192.0.2.11", "192.0.2.12"), ("192.0.2.12", "192.0.2.11")})
    check("NFS is limited to TCP 111 and 2049 on the storage node",
          first["nodes"]["pve-1"]["desired"], [{
              "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "111,2049",
              "source": "203.0.113.22/32", "enable": 1,
              "comment": "homelab-infra: base storage-nfs-pve-1-1",
          }])
    check("per-app API rules are not part of the baseline namespace",
          all(rule["comment"].startswith("homelab-infra: base ")
              for rule in first["cluster"]["desired"]), True)

    joining = copy.deepcopy(proxmox)
    joining["nodes"]["pve-3"] = "192.0.2.13"
    joining_payload = copy.deepcopy(empty)
    joining_payload["proxmox"] = joining
    joining_plan = run_plan(joining_payload)
    peer_pairs = {
        (rule.get("source"), rule.get("dest"))
        for rule in joining_plan["cluster"]["desired"]
        if rule.get("comment", "").startswith("homelab-infra: base cluster-")
    }
    check("adding a joining node opens it as a peer before it joins", {
        ("192.0.2.13", "192.0.2.11"), ("192.0.2.11", "192.0.2.13"),
        ("192.0.2.13", "192.0.2.12"), ("192.0.2.12", "192.0.2.13"),
    } <= peer_pairs, True)
    check("joining nodes are not queried for host rules before joining",
          set(joining_plan["nodes"]), {"pve-1", "pve-2"})

    converged = copy.deepcopy(empty)
    converged["cluster_options"] = {
        "enable": 1, "policy_in": "DROP", "policy_out": "ACCEPT"}
    converged["cluster_rules"] = [
        {**rule, "pos": index, "digest": "fixture"}
        for index, rule in enumerate(first["cluster"]["desired"], start=1)
    ]
    converged["node_options"] = {"pve-1": {"enable": 1}, "pve-2": {"enable": 1}}
    converged["node_rules"] = {
        "pve-1": [{**rule, "pos": 1, "digest": "fixture"}
                   for rule in first["nodes"]["pve-1"]["desired"]],
        "pve-2": [],
    }
    repeat = run_plan(converged)
    check("a repeat baseline run is converged", repeat["changed"], False)
    check("a converged run creates no datacenter rules", repeat["cluster"]["create"], [])
    check("a converged run removes no node rules", {
        node: state["delete"] for node, state in repeat["nodes"].items()
    }, {"pve-1": [], "pve-2": []})

    no_op = copy.deepcopy(empty)
    no_op["proxmox"]["firewall"]["enabled"] = False
    disabled = run_plan(no_op)
    check("disabled firewall config changes nothing", disabled["changed"], False)
    check("disabled firewall config emits no rules", disabled["cluster"]["create"], [])
    disabled_playbook_noop()
    invalid_enabled = copy.deepcopy(no_op)
    invalid_enabled["proxmox"]["firewall"]["enabled"] = "false"
    run_plan(invalid_enabled, expect_success=False)

    legacy = copy.deepcopy(empty)
    legacy["node_rules"] = {
        "pve-1": [
            {"pos": 1, "type": "in", "action": "ACCEPT", "proto": "tcp",
             "dport": "22,8006,3128", "source": "198.51.100.0/24", "enable": 1},
            {"pos": 2, "type": "in", "action": "ACCEPT", "proto": "icmp",
             "source": "198.51.100.0/24", "enable": 1},
            {"pos": 3, "type": "in", "action": "ACCEPT", "proto": "tcp",
             "dport": "60000:60050", "source": "192.0.2.12", "enable": 1},
            {"pos": 4, "type": "in", "action": "ACCEPT", "proto": "udp",
             "dport": "5405:5412", "source": "192.0.2.12", "enable": 1},
            {"pos": 5, "type": "in", "action": "ACCEPT", "proto": "tcp",
             "dport": "22,111,2049,3260", "source": "203.0.113.22", "enable": 1},
            {"pos": 6, "type": "in", "action": "ACCEPT", "proto": "tcp",
             "dport": "8080", "source": "203.0.113.99", "enable": 1},
            {"pos": 7, "type": "in", "action": "ACCEPT", "proto": "udp",
             "dport": "5405:5412", "source": "203.0.113.22", "enable": 1},
            {"pos": 8, "type": "in", "action": "ACCEPT", "proto": "tcp",
             "dport": "8006", "source": "192.0.2.12", "enable": 1,
             "comment": "homelab-infra: pdm pdm-proxmox-api"},
        ],
        "pve-2": [],
    }
    migrated = run_plan(legacy)
    check("only the exact legacy helper rules are removed",
          {rule["pos"] for rule in migrated["nodes"]["pve-1"]["delete"]},
          {1, 2, 3, 4, 5})
    check("foreign unowned host rules survive takeover",
          any(rule.get("dport") == "8080"
              for rule in legacy["node_rules"]["pve-1"]
              if rule["pos"] not in {item["pos"] for item in migrated["nodes"]["pve-1"]["delete"]}),
          True)
    check("PDM API rule survives takeover",
          any(rule.get("comment") == "homelab-infra: pdm pdm-proxmox-api"
              for rule in legacy["node_rules"]["pve-1"]
              if rule["pos"] not in {item["pos"] for item in migrated["nodes"]["pve-1"]["delete"]}),
          True)
    check("peer-shaped rules from non-node storage clients are preserved",
          any(rule.get("source") == "203.0.113.22"
              and rule.get("dport") == "5405:5412"
              for rule in legacy["node_rules"]["pve-1"]
              if rule["pos"] not in {item["pos"]
                                      for item in migrated["nodes"]["pve-1"]["delete"]}),
          True)

    broad_legacy = copy.deepcopy(empty)
    broad_legacy["proxmox"]["firewall"]["storage_clients"] = ["203.0.113.0/24"]
    broad_legacy["node_rules"] = {
        "pve-1": [{
            "pos": 1, "type": "in", "action": "ACCEPT", "proto": "tcp",
            "dport": "22,111,2049,3260", "source": "203.0.113.22", "enable": 1,
        }],
        "pve-2": [],
    }
    broad_plan = run_plan(broad_legacy)
    check("a broad storage subnet does not claim an ambiguous unowned host rule",
          broad_plan["nodes"]["pve-1"]["delete"], [])

    valid_doctor = doctor({
        "storage_nodes": ["fixture-storage-node"],
        "firewall": {
            "enabled": True,
            "management_sources": ["198.51.100.0/24"],
            "storage_clients": ["203.0.113.0/24"],
        },
    })
    check("Config Doctor accepts enabled documented firewall inputs",
          "ERROR" not in valid_doctor, True)
    invalid_doctor = doctor({
        "storage_nodes": ["missing-node"],
        "firewall": {
            "enabled": True,
            "management_sources": ["not-a-cidr"],
            "storage_clients": [],
        },
    }, invalid=True)
    check("Config Doctor names invalid firewall CIDRs and storage nodes",
          "proxmox.firewall.management_sources.0" in invalid_doctor
          and "proxmox.storage_nodes" in invalid_doctor, True)

    if FAILURES:
        print("Proxmox firewall baseline: FAILED")
        print("\n".join(f"- {failure}" for failure in FAILURES))
        return 1
    print("Proxmox firewall baseline renders, migrates only owned rules, and converges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
