#!/usr/bin/env python3
"""Render production PCI assignment tasks: ROM boot workaround, updates and provenance."""
import sys
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ansible/scripts"))
from device_mode_validation import validate_device_modes  # noqa: E402

TASKS = yaml.safe_load((ROOT / "ansible/tasks/proxmox/attach-pci-passthrough.yml").read_text())


def task(name):
    return next(t for t in TASKS if t["name"] == "PCI passthrough | " + name)


def render(value, context):
    return Templar(loader=DataLoader(), variables=context).template(value)


def assignment(existing, rombar="absent", tagged=True, legacy="absent", guest_lines=None):
    device = {"id": "0000:00:02.0", "mapping": "intel-igpu-2"}
    if rombar != "absent":
        device["rombar"] = rombar
    if legacy != "absent":
        device["legacy_igd"] = legacy
    context = {
        "pci_passthrough_device": device, "_pci_vmid": "123",
        "_pci_already_present": bool(existing),
        "_pci_tags": ["_+lab"] + (["_.dev+intel-igpu-2"] if tagged else []),
        "_pci_all_configs": {"results": [{"item": {"vmid": 123}, "stdout_lines": existing + (guest_lines or [])}]},
    }
    read = task("Read this guest's existing assignment")
    context["_pci_matching_lines"] = render(read["vars"]["_pci_matching_lines"], context)
    context["_pci_existing_line"] = render(read["ansible.builtin.set_fact"]["_pci_existing_line"], context)
    context["_pci_rombar_changed"] = render(task("Resolve whether the option ROM needs updating")["ansible.builtin.set_fact"]["_pci_rombar_changed"], context)
    legacy_task = task("Resolve legacy IGD settings")
    context["_pci_guest_lines"] = render(legacy_task["vars"]["_pci_guest_lines"], context)
    context["_pci_legacy_changed"] = render(legacy_task["ansible.builtin.set_fact"]["_pci_legacy_changed"], context)
    guard = task("Require provenance before updating an existing assignment")
    context["_pci_provenance_tag"] = render(guard["vars"]["_pci_provenance_tag"], context)
    permitted = render("{{ " + guard["ansible.builtin.assert"]["that"] + " }}", context)
    if (context["_pci_rombar_changed"] or context["_pci_legacy_changed"]) and not permitted:
        return "refused", None
    block = task("Stop, assign, and restart the guest")
    if not render("{{ " + block["when"] + " }}", context):
        return "unchanged", None
    index = task("Resolve the hostpci index for this guest")
    context["_pci_used_indices"] = render(index["vars"]["_pci_used_indices"], context)
    context["_pci_next_index"] = render(index["ansible.builtin.set_fact"]["_pci_next_index"], context)
    command = next(t for t in block["block"] if t["name"].endswith("Assign the device and its provenance tag"))
    for key, value in command["vars"].items():
        context[key] = render(value, context)
    return "changed", render(command["ansible.builtin.command"]["argv"], context)


assert assignment([])[1][4] == "mapping=intel-igpu-2"
assert assignment([], False)[1][4] == "mapping=intel-igpu-2,rombar=0"
for rombar in ("absent", None, "", False):
    assert assignment(["hostpci3: mapping=intel-igpu-2,rombar=0"], rombar)[0] == "unchanged"
assert assignment(["hostpci3: mapping=intel-igpu-2"], True)[0] == "unchanged"
status, argv = assignment(["hostpci3: mapping=intel-igpu-2,pcie=1,rombar=1,romfile=fixture.rom"], False)
assert status == "changed" and argv[3] == "-hostpci3", argv
assert argv[4] == "mapping=intel-igpu-2,pcie=1,romfile=fixture.rom,rombar=0", argv
assert assignment(["hostpci3: mapping=intel-igpu-2,rombar=0"], True)[1][4] == "mapping=intel-igpu-2"
assert assignment(["hostpci3: mapping=intel-igpu-2"], False, tagged=False)[0] == "refused"
assert assignment(["hostpci3: 0000:00:02.0"], False)[1][3:5] == ["-hostpci3", "0000:00:02.0,rombar=0"]

# Ansible may render a default(none) leaf as an empty string rather than Python None.
for caller in ("stack/find-or-create-host.yml", "proxmox/provision-app-vm.yml"):
    tasks = yaml.safe_load((ROOT / "ansible/tasks" / caller).read_text())
    attach = next(t for t in tasks if t.get("ansible.builtin.include_tasks", "").endswith("attach-pci-passthrough.yml"))
    context = {"homelabinfra_config": {"proxmox": {"devices": {"fixture": {}}}},
               "_stack_gpu_device": "fixture", "app_config": {"app": {"gpu_device": "fixture"}}}
    omitted = render(attach["vars"]["pci_passthrough_device"]["rombar"], context)
    assert assignment(["hostpci3: mapping=intel-igpu-2,rombar=0"], omitted)[0] == "unchanged"

for value in (True, False, "false"):
    errors = []
    validate_device_modes({"devices": {"fixture": {
        "kind": "igpu", "mode": "dedicated", "node": "fixture-node",
        "identifiers": ["0000:00:02.0"], "rombar": value,
    }}}, lambda key, message: errors.append((key, message)))
    assert bool(errors) == (value == "false"), errors

print("PCI ROM BAR assignment, repeat, slot preservation, provenance and validation: OK")

assert assignment([], legacy=True)[1][4] == "mapping=intel-igpu-2,legacy-igd=1"
assert assignment([], legacy=True)[1][-2:] == ["-vga", "none"]
status, argv = assignment(["hostpci3: mapping=intel-igpu-2,rombar=0,romfile=fixture.rom"], True, legacy=True)
assert status == "changed" and argv[3:5] == ["-hostpci3", "mapping=intel-igpu-2,romfile=fixture.rom,legacy-igd=1"]
assert assignment(["hostpci3: mapping=intel-igpu-2,legacy-igd=1"], legacy=True, guest_lines=["vga: none"])[0] == "unchanged"
assert assignment(["hostpci3: mapping=intel-igpu-2,legacy-igd=1"], legacy=True)[0] == "changed"
assert assignment(["hostpci3: mapping=intel-igpu-2"], legacy=True, tagged=False)[0] == "refused"
assert assignment(["hostpci3: mapping=intel-igpu-2,legacy-igd=1"], legacy=False)[1][4] == "mapping=intel-igpu-2"
assert assignment(["hostpci3: mapping=intel-igpu-2,legacy-igd=1"], False)[1][4] == "mapping=intel-igpu-2,legacy-igd=1,rombar=0"
for kind, legacy, rombar, valid in [("igpu", True, True, True), ("igpu", False, False, True), ("igpu", "true", True, False), ("gpu", True, True, False), ("igpu", True, False, False)]:
    errors = []
    validate_device_modes({"devices": {"fixture": {
        "kind": kind, "mode": "dedicated", "node": "fixture-node",
        "identifiers": ["0000:00:02.0"], "rombar": rombar, "legacy_igd": legacy,
    }}}, lambda key, message: errors.append((key, message)))
    assert bool(errors) != valid, errors
print("Legacy IGD assignment, VGA convergence, repeat, omission and device validation: OK")

legacy_guard = task("Require legacy IGD firmware and chipset")["ansible.builtin.assert"]["that"]
for device, lines, valid in [
    ({"legacy_igd": True}, [], True),
    ({"legacy_igd": True, "rombar": False}, [], False),
    ({"legacy_igd": True, "rombar": True}, ["machine: pc-q35-9.0"], False),
    ({"legacy_igd": True}, ["bios: ovmf"], False),
    ({"legacy_igd": True, "pcie": True}, [], False),
]:
    ctx = {"pci_passthrough_device": device, "_pci_guest_lines": lines, "_pci_existing_line": ""}
    assert all(render("{{ " + condition + " }}", ctx) for condition in legacy_guard) == valid
print("Legacy IGD firmware, chipset, ROM and PCIe guards: OK")

# Distinct node-scoped iGPUs may use different modes. Unscoped shared failover
# and a shared declaration on a dedicated node must still be rejected.
for shared_node, valid in [("fixture-shared", True), (None, False), ("fixture-dedicated", False)]:
    shared = {"kind": "igpu", "mode": "shared", "identifiers": ["/dev/dri/renderD128"]}
    if shared_node is not None:
        shared["node"] = shared_node
    errors = []
    validate_device_modes({"node": "fixture-fallback", "devices": {
        "dedicated": {"kind": "igpu", "mode": "dedicated", "node": "fixture-dedicated",
                      "identifiers": ["0000:00:02.0"]},
        "shared": shared,
    }}, lambda key, message: errors.append((key, message)))
    assert bool(errors) != valid, errors
print("Mixed iGPU modes require isolated explicit shared placement: OK")
