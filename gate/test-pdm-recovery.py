#!/usr/bin/env python3
"""Keep PDM lifecycle wiring and recovery aligned with its authored config shape."""

from pathlib import Path
import json
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def tasks(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        for key in ("block", "rescue", "always"):
            nested = item.get(key, [])
            if isinstance(nested, list):
                yield from tasks(nested)


def main():
    restore_text = (ROOT / "ansible/playbooks/maintenance/restore-pdm.yml").read_text()
    assert "proxmox.vm.vmid" not in restore_text
    assert "proxmox.vmid" in restore_text
    assert "pdm_target_config.proxmox.vmid" in restore_text

    deploy = load("ansible/playbooks/apps/proxmox-datacenter-manager.yml")[0]
    deploy_tasks = list(tasks(deploy["tasks"]))
    deploy_names = [item.get("name", "") for item in deploy_tasks]
    for name in (
        "Wire PDM Proxmox API access",
        "Wire PDM Proxmox host API access",
        "Wire reverse proxy",
        "Wire SSO",
        "Register uptime monitor",
        "Wire DNS",
    ):
        assert name in deploy_names, f"PDM deploy must include {name}"
    deploy_vars = deploy["vars"]
    assert deploy_vars["wiring_upstream_scheme"] == "https"
    assert deploy_vars["wiring_upstream_tls_insecure_skip_verify"] is True
    deploy_text = (ROOT / "ansible/playbooks/apps/proxmox-datacenter-manager.yml").read_text()
    assert "pdm_config.proxmox.vlan" in deploy_text
    assert ",tag=" in deploy_text
    assert 'installer_vm_boot: "order=scsi0;ide2"' in deploy_text
    pdm_firewall = load("ansible/tasks/wiring/pdm-proxmox-egress.yml")
    pdm_firewall_tasks = list(tasks(pdm_firewall))
    assert any("homelabinfra_config.proxmox.nodes" in str(item) for item in pdm_firewall_tasks)
    assert any("getent" in str(item) and "ahostsv4" in str(item) for item in pdm_firewall_tasks)
    assert any("8006" in str(item) and "destination" in str(item)
               and "proxmox_api_sequence" in str(item) for item in pdm_firewall_tasks)
    assert any("../firewall/open-app-access.yml" in str(item) for item in pdm_firewall_tasks)
    pdm_defaults = load("ansible/vars/app-defaults/proxmox-datacenter-manager.yml")
    assert pdm_defaults["proxmox_datacenter_manager_defaults"]["firewall"]["proxmox_api_sequence"] == 250
    example = load("config.example/apps/proxmox-datacenter-manager.example.yml")
    assert example["firewall"]["proxmox_api_sequence"] == 250
    remote_item = example["remotes"][0]["vaultwarden"]["item"]
    assert remote_item.startswith("homelab-infra/")
    assert "/" not in remote_item.removeprefix("homelab-infra/")
    runtime = ROOT / "ansible/scripts/vault-runtime.py"
    parsed = subprocess.run(
        ["python3", str(runtime), "--require", remote_item],
        input=json.dumps([{"name": remote_item, "fields": []}]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert remote_item.rsplit("/", 1)[-1] in parsed.stdout
    host_firewall = load("ansible/tasks/wiring/pdm-proxmox-host-firewall.yml")
    host_firewall_tasks = list(tasks(host_firewall))
    host_firewall_text = (ROOT / "ansible/tasks/wiring/pdm-proxmox-host-firewall-node.yml").read_text()
    assert any("map(attribute='pve_node')" in str(item) for item in host_firewall_tasks)
    assert "pvesh" in host_firewall_text and "create" in host_firewall_text
    assert "--action" in host_firewall_text and "ACCEPT" in host_firewall_text
    assert "--type" in host_firewall_text and "in" in host_firewall_text
    assert "--proto" in host_firewall_text and "tcp" in host_firewall_text
    assert "--dport" in host_firewall_text and '"8006"' in host_firewall_text
    assert "--source" in host_firewall_text and "pdm_config.proxmox.ip_address" in host_firewall_text
    assert "homelab-infra: {{ instance }} pdm-proxmox-api" in host_firewall_text
    assert "pdm_config.proxmox.node" in host_firewall_text
    host_firewall_remove = (ROOT / "ansible/tasks/unwiring/pdm-proxmox-host-firewall.yml").read_text()
    assert "homelabinfra_config.proxmox.nodes" in host_firewall_remove
    host_firewall_remove_node = (ROOT / "ansible/tasks/unwiring/pdm-proxmox-host-firewall-node-remove.yml").read_text()
    assert "homelab-infra: {{ instance }} pdm-proxmox-api" in host_firewall_remove_node
    assert "pvesh" in host_firewall_remove_node and "delete" in host_firewall_remove_node
    installer_vm = (ROOT / "ansible/tasks/proxmox/create-installer-vm.yml").read_text()
    assert "installer_vm_boot | default('order=ide2;scsi0')" in installer_vm
    installer_tasks = yaml.safe_load(installer_vm)
    status = next(item for item in installer_tasks if item.get("name") == "Installer VM | Read state before start")
    start = next(item for item in installer_tasks if item.get("name") == "Installer VM | Start installer")
    assert status["ansible.builtin.command"]["argv"][:2] == ["qm", "status"]
    assert "status: running" in start["when"]
    create = next(item for item in installer_tasks if item.get("name") == "Installer VM | Create project-owned appliance VM")
    assert create["community.proxmox.proxmox_kvm"]["update"] is False
    assert create["when"] == "_installer_vm_config.rc != 0"
    inspect_config = next(item for item in installer_tasks if item.get("name") == "Installer VM | Read current managed VM configuration")
    assert inspect_config["ansible.builtin.command"]["argv"][:2] == ["pvesh", "get"]
    update = next(item for item in installer_tasks if item.get("name") == "Installer VM | Update changed managed appliance configuration")
    assert update["community.proxmox.proxmox_kvm"]["update"] is True
    assert update["when"] == [
        "_installer_vm_config.rc == 0",
        "not (_installer_vm_configuration_matches | bool)",
    ]

    remove = load("ansible/playbooks/maintenance/remove-pdm.yml")[0]
    remove_tasks = list(tasks(remove["tasks"]))
    remove_names = [item.get("name", "") for item in remove_tasks]
    inspect = remove_names.index("PDM | Inspect requested VM")
    lookup = remove_tasks[inspect]["ansible.builtin.command"]["argv"]
    assert lookup[:3] == ["pvesh", "get", "/cluster/resources"]
    ownership = remove_names.index("PDM | Assert managed VM ownership")
    destroy = remove_names.index("PDM | Destroy managed appliance VM")
    assert inspect < ownership < remove_names.index("Unwire reverse proxy")
    assert remove_names.index("Unwire PDM Proxmox host API access") < destroy
    for name in (
        "Unwire PDM Proxmox API access",
        "Unwire PDM Proxmox host API access",
        "Unwire reverse proxy",
        "Unwire SSO",
        "Unregister uptime monitor",
        "Unwire DNS",
    ):
        assert name in remove_names, f"PDM remove must include {name}"
        assert remove_names.index(name) < destroy, f"{name} must run before VM destruction"
    assert remove_names.index("PDM | Recheck managed VM ownership before destruction") < destroy
    assert remove_names.index("PDM | Verify the VM is absent") > destroy

    caddy = load("ansible/tasks/wiring/caddy.yml")
    route = next(
        item for item in tasks(caddy)
        if item.get("name") == "Wire Caddy | Register route"
    )
    transport = route["vars"]["_caddy_app_transport"]
    assert "wiring_upstream_scheme" in transport
    assert "insecure_skip_verify" in transport
    print("PDM lifecycle and recovery checks passed")


if __name__ == "__main__":
    main()
