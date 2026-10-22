#!/usr/bin/env python3
"""Check optional VM balloon configuration and preflight coverage."""

from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
task_dir = repo / "ansible/tasks/proxmox"


def load_tasks(path):
    return yaml.safe_load(path.read_text())


def task_index(tasks, name):
    for index, task in enumerate(tasks):
        if task.get("name") == name:
            return index
    raise AssertionError(f"task not found: {name}")


def include_index(tasks, filename):
    for index, task in enumerate(tasks):
        if task.get("ansible.builtin.include_tasks") == filename:
            return index
    raise AssertionError(f"include not found: {filename}")


validator = load_tasks(task_dir / "validate-vm-balloon.yml")[0]["ansible.builtin.assert"]
assert "proxmox_vm_balloon_mib | string is match('^[1-9][0-9]*$')" in validator["that"]
assert "proxmox_vm_memory_mib | string is match('^[1-9][0-9]*$')" in validator["that"]
assert "(proxmox_vm_balloon_mib | int) <= (proxmox_vm_memory_mib | int)" in validator["that"]

resolver = load_tasks(task_dir / "resolve-vm-balloon.yml")[0]["ansible.builtin.set_fact"]
assert "app_config" in resolver["_proxmox_vm_balloon_requested"]
assert "homelabinfra_config" in resolver["_proxmox_vm_balloon_requested"]
assert "app_config" in resolver["_proxmox_vm_memory_max_mib"]
assert "proxmox_vm_balloon_memory_fallback" in resolver["_proxmox_vm_memory_max_mib"]

create = load_tasks(task_dir / "vm-create.yml")
create_resolver = include_index(create, "resolve-vm-balloon.yml")
create_validation = include_index(create, "validate-vm-balloon.yml")
assert create_resolver < create_validation < task_index(create, "Create VM"), "resolve and validate before VM create"
create_args = load_tasks(task_dir / "vm-create.yml")[task_index(
    create, "Build VM module arguments from the community.proxmox.proxmox_kvm allowlist"
)]["vars"]["vm_module_keys"]
assert "balloon" in create_args, create_args
create_module_args = str(create[task_index(
    create, "Build VM module arguments from the community.proxmox.proxmox_kvm allowlist"
)]["ansible.builtin.set_fact"]["vm_module_args"])
assert "rejectattr('key', 'equalto', 'balloon')" in create_module_args
assert "_proxmox_vm_memory_max_mib | int" in create_module_args
assert "_proxmox_vm_balloon_configured | bool" in create_module_args

clone = load_tasks(task_dir / "vm-clone.yml")
clone_resolver = include_index(clone, "resolve-vm-balloon.yml")
clone_validation = include_index(clone, "validate-vm-balloon.yml")
assert clone_resolver < clone_validation < task_index(clone, "VM clone | Clone the template")
update = clone[task_index(clone, "VM clone | Apply per-VM configuration")][
    "community.proxmox.proxmox_kvm"
]
assert "_proxmox_vm_balloon_requested | int" in update["balloon"]
assert "_proxmox_vm_balloon_configured | bool" in update["balloon"]
assert "_proxmox_vm_memory_max_mib | int" in update["memory"]
assert "_proxmox_vm_balloon_configured | bool" in update["memory"]
assert update["update_unsafe"] == "{{ _vmc_clone.changed | default(false) }}"

template = load_tasks(task_dir / "ensure-cloud-template.yml")
template_resolver = include_index(template, "resolve-vm-balloon.yml")
template_validation = include_index(template, "validate-vm-balloon.yml")
assert template_resolver < template_validation < task_index(
    template, "Make Proxmox nodes addressable for delegation"
), "validate before any template operation"

installer = load_tasks(task_dir / "create-installer-vm.yml")
installer_validation = include_index(installer, "validate-vm-balloon.yml")
for name in (
    "Installer VM | Create project-owned appliance VM",
    "Installer VM | Update changed managed appliance configuration",
):
    assert installer_validation < task_index(installer, name), name
    module_args = installer[task_index(installer, name)]["community.proxmox.proxmox_kvm"]
    assert module_args["balloon"] == "{{ installer_vm.balloon | default(omit, true) }}"

match_expression = str(
    installer[task_index(installer, "Installer VM | Compare requested VM configuration")][
        "ansible.builtin.set_fact"
    ]["_installer_vm_configuration_matches"]
)
assert "installer_vm.balloon is none" in match_expression
assert "_installer_vm_current_config.balloon" in match_expression

ensure = load_tasks(task_dir / "ensure-guest-running.yml")
ensure_config = ensure[task_index(
    ensure,
    "Ensure running | Read the guest config needed for memory checks or balloon convergence",
)]
assert "_egr_balloon_requested | string | length > 0" in str(ensure_config["when"])
ensure_validation = task_index(ensure, "Ensure running | Validate the configured balloon minimum")
ensure_update = task_index(ensure, "Ensure running | Converge the configured VM balloon minimum")
ensure_update_args = ensure[ensure_update]["community.proxmox.proxmox_kvm"]
assert ensure_validation < ensure_update
assert ensure_update_args["update"] is True
assert ensure_update_args["memory"] == "{{ _egr_balloon_memory_mib | int }}"
assert ensure_update_args["balloon"] == "{{ _egr_balloon_requested | int }}"
assert ensure[ensure_update]["no_log"] is True
assert "_egr_guest_config_data.balloon" in str(ensure[ensure_update]["when"])
assert "_egr_guest_config_data.memory" in str(ensure[ensure_update]["when"])
assert "_egr_balloon_requested is not none" in str(ensure[ensure_update]["when"])

pdm = load_tasks(repo / "ansible/playbooks/apps/proxmox-datacenter-manager.yml")
assert any(
    task.get("ansible.builtin.include_tasks") == "../../tasks/proxmox/create-installer-vm.yml"
    and "pdm_config.proxmox.vm.balloon" in str(task.get("vars", {}))
    for play in pdm
    for task in play.get("tasks", [])
), "PDM must pass its optional balloon setting to the installer VM path"

contract = (repo / "ansible/vars/CONTRACT.md").read_text()
example = (repo / "config.example/apps/home-assistant.example.yml").read_text()
assert "VM instances may set `proxmox.vm.balloon`" in contract
assert "balloon: 2048 # optional minimum in MiB" in example

print("VM balloon minimum is validated before mutations and converges only when configured")
