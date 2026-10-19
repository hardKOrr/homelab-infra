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
    for name in ("Wire reverse proxy", "Wire SSO", "Register uptime monitor", "Wire DNS"):
        assert name in deploy_names, f"PDM deploy must include {name}"
    deploy_vars = deploy["vars"]
    assert deploy_vars["wiring_upstream_scheme"] == "https"
    assert deploy_vars["wiring_upstream_tls_insecure_skip_verify"] is True
    deploy_text = (ROOT / "ansible/playbooks/apps/proxmox-datacenter-manager.yml").read_text()
    assert "pdm_config.proxmox.vlan" in deploy_text
    assert ",tag=" in deploy_text
    assert 'installer_vm_boot: "order=scsi0;ide2"' in deploy_text
    example = load("config.example/apps/proxmox-datacenter-manager.example.yml")
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
    installer_vm = (ROOT / "ansible/tasks/proxmox/create-installer-vm.yml").read_text()
    assert "installer_vm_boot | default('order=ide2;scsi0')" in installer_vm
    installer_tasks = yaml.safe_load(installer_vm)
    status = next(item for item in installer_tasks if item.get("name") == "Installer VM | Read state before start")
    start = next(item for item in installer_tasks if item.get("name") == "Installer VM | Start installer")
    assert status["ansible.builtin.command"]["argv"][:2] == ["qm", "status"]
    assert "status: running" in start["when"]

    remove = load("ansible/playbooks/maintenance/remove-pdm.yml")[0]
    remove_tasks = list(tasks(remove["tasks"]))
    remove_names = [item.get("name", "") for item in remove_tasks]
    inspect = remove_names.index("PDM | Inspect requested VM")
    lookup = remove_tasks[inspect]["ansible.builtin.command"]["argv"]
    assert lookup[:3] == ["pvesh", "get", "/cluster/resources"]
    ownership = remove_names.index("PDM | Assert managed VM ownership")
    destroy = remove_names.index("PDM | Destroy managed appliance VM")
    assert inspect < ownership < remove_names.index("Unwire reverse proxy")
    for name in ("Unwire reverse proxy", "Unwire SSO", "Unregister uptime monitor", "Unwire DNS"):
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
