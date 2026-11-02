#!/usr/bin/env python3
"""Exercise the VM HA reconciler with isolated Proxmox command fixtures.

Checks: opt-in HA resource creation, idempotence, owned-resource removal, shared-storage
refusal and Remove ordering.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
TASK = REPO / "ansible/tasks/proxmox/reconcile-vm-ha.yml"
MARKER = "managed by homelab-infra"


PVE_SHIM = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["HA_FIXTURE_DIR"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(["pvesh", *args]) + "\n")
if args[:2] == ["get", "/nodes/localhost/qemu/456/config"]:
    print((root / "guest.json").read_text())
elif args[:2] == ["get", "/storage"]:
    print((root / "storage.json").read_text())
elif args[:2] == ["get", "/cluster/ha/resources"]:
    print((root / "resources.json").read_text())
else:
    raise SystemExit("unexpected pvesh request: " + " ".join(args))
'''


HA_SHIM = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["HA_FIXTURE_DIR"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(["ha-manager", *args]) + "\n")
resources = json.loads((root / "resources.json").read_text())
sid = args[1]
resource = next((item for item in resources if item.get("sid") == sid), None)
if args[0] == "add":
    resources.append({"sid": sid, "state": args[args.index("--state") + 1],
                      "comment": args[args.index("--comment") + 1]})
elif args[0] == "set":
    if resource is None:
        raise SystemExit("resource is absent")
    resource["state"] = args[args.index("--state") + 1]
    resource["comment"] = args[args.index("--comment") + 1]
elif args[0] == "remove":
    resources = [item for item in resources if item.get("sid") != sid]
else:
    raise SystemExit("unexpected ha-manager request: " + " ".join(args))
(root / "resources.json").write_text(json.dumps(resources))
'''


def ansible_command():
    configured = os.environ.get("GATE_ANSIBLE_PLAYBOOK")
    candidate = Path(configured) if configured else Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("ansible-playbook")
    if found:
        return found
    raise AssertionError("ansible-playbook is unavailable")


def run_case(
    case, *, enabled=None, require_owned=False, disk_storage="shared-vm",
    resource=None, cdrom_slots=False,
):
    with tempfile.TemporaryDirectory(prefix="homelab-proxmox-ha-") as temp:
        root = Path(temp)
        bindir = root / "bin"
        bindir.mkdir()
        for name, content in (("pvesh", PVE_SHIM), ("ha-manager", HA_SHIM)):
            executable = bindir / name
            executable.write_text(content)
            executable.chmod(0o755)

        guest = {
            "name": "test-vm",
            "tags": "_+lab;_-debian",
            "scsi0": f"{disk_storage}:vm-456-disk-0,size=20G",
            "efidisk0": "shared-vm:vm-456-disk-1,size=4M",
        }
        if cdrom_slots:
            guest.update({
                "ide2": "cdrom,media=cdrom",
                "sata2": "local:iso/test.iso,media=cdrom",
            })
        (root / "guest.json").write_text(json.dumps(guest))
        storages = [
            {"storage": "shared-vm", "shared": 1},
            {"storage": "local-lvm", "shared": 0},
        ]
        (root / "storage.json").write_text(json.dumps(storages))
        (root / "resources.json").write_text(json.dumps([resource] if resource else []))
        playbook = root / "play.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "connection": "local",
                        "gather_facts": False,
                        "vars": {
                            "proxmox_ha_node": "localhost",
                            "proxmox_ha_vmid": 456,
                            "proxmox_ha_name": "test-vm",
                            "proxmox_ha_require_owned": require_owned,
                        },
                        "tasks": [{"ansible.builtin.include_tasks": str(TASK)}],
                    }
                ],
                sort_keys=False,
            )
        )
        if enabled is not None:
            play_data = yaml.safe_load(playbook.read_text())
            play_data[0]["vars"]["proxmox_ha_enabled"] = enabled
            playbook.write_text(yaml.safe_dump(play_data, sort_keys=False))
        env = os.environ | {
            "HA_FIXTURE_DIR": str(root),
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "ANSIBLE_CONFIG": str(REPO / "ansible/ansible.cfg"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_STDOUT_CALLBACK": "default",
        }
        result = subprocess.run(
            [ansible_command(), "-i", "localhost,", str(playbook)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
        calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
        resources_after = json.loads((root / "resources.json").read_text())
        return result, calls, resources_after


def module_tests():
    owned = {"sid": "vm:456", "state": "started", "comment": MARKER}

    result, calls, resources = run_case("enable", enabled=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if call[0] == "ha-manager"] == [
        ["ha-manager", "add", "vm:456", "--state", "started", "--comment", MARKER]
    ], calls
    assert resources == [owned], resources

    result, calls, resources = run_case(
        "cdrom-iso-slots", enabled=True, cdrom_slots=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if call[0] == "ha-manager"] == [
        ["ha-manager", "add", "vm:456", "--state", "started", "--comment", MARKER]
    ], calls
    assert resources == [owned], resources

    result, calls, resources = run_case("enabled-converged", enabled=True, resource=owned)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [owned], resources

    result, calls, resources = run_case(
        "enabled-correct-state", enabled=True,
        resource={"sid": "vm:456", "state": "stopped", "comment": MARKER},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call[1:3] for call in calls if call[0] == "ha-manager"] == [
        ["set", "vm:456"]
    ], calls
    assert resources == [owned], resources

    result, calls, resources = run_case("disabled-no-resource")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [], resources

    result, calls, resources = run_case(
        "disabled-owned-resource", enabled=False, resource=owned
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call[1:3] for call in calls if call[0] == "ha-manager"] == [
        ["remove", "vm:456"]
    ], calls
    assert resources == [], resources

    unowned = {"sid": "vm:456", "state": "started", "comment": "operator-owned"}
    result, calls, resources = run_case("disabled-unowned-resource", resource=unowned)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [unowned], resources

    result, calls, resources = run_case("local-disk", enabled=True, disk_storage="local-lvm")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "disk scsi0" in result.stdout and "non-shared storage local-lvm" in result.stdout, result.stdout
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [], resources

    result, calls, resources = run_case("unowned-enable", enabled=True, resource=unowned)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ownership comment" in result.stdout, result.stdout
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [unowned], resources

    result, calls, resources = run_case(
        "remove-owned", require_owned=True, resource=owned
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call[1:3] for call in calls if call[0] == "ha-manager"] == [
        ["remove", "vm:456"]
    ], calls
    assert resources == [], resources

    result, calls, resources = run_case(
        "remove-unowned", require_owned=True, resource=unowned
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Refusing to destroy VM 456" in result.stdout, result.stdout
    assert not [call for call in calls if call[0] == "ha-manager"], calls
    assert resources == [unowned], resources


def wiring_tests():
    def tasks(path):
        return yaml.safe_load(path.read_text())

    for filename, task_name in (
        ("vm-clone.yml", "VM clone | Reconcile the guest's optional HA resource"),
        ("vm-create.yml", "Reconcile the VM's optional HA resource"),
        ("ensure-guest-running.yml", "Ensure running | Reconcile the deployed VM's optional HA resource"),
        ("create-installer-vm.yml", "Installer VM | Reconcile its optional HA resource"),
    ):
        items = tasks(REPO / "ansible/tasks/proxmox" / filename)
        task = next(item for item in items if item.get("name") == task_name)
        assert task.get("ansible.builtin.include_tasks") == "reconcile-vm-ha.yml", task

    destroy = tasks(REPO / "ansible/tasks/proxmox/destroy-owned-vm.yml")
    ha = next(i for i, item in enumerate(destroy) if item.get("name") == "Destroy owned VM | Remove its homelab-infra HA resource first")
    stop = next(i for i, item in enumerate(destroy) if item.get("name") == "Destroy owned VM | Read the guest state")
    remove = next(i for i, item in enumerate(destroy) if item.get("name") == "Destroy owned VM | Destroy the stopped guest")
    assert ha < stop < remove, "Remove must withdraw the HA resource before guest shutdown and destruction"

    contract = (REPO / "ansible/vars/contract/app-layering.md").read_text()
    example = (REPO / "config.example/apps/k3s-cluster.example.yml").read_text()
    pdm = yaml.safe_load((REPO / "ansible/playbooks/apps/proxmox-datacenter-manager.yml").read_text())
    assert "`proxmox.vm.ha: true`" in contract
    assert "ha: true" in example
    assert any(
        task.get("ansible.builtin.include_tasks") == "../../tasks/proxmox/create-installer-vm.yml"
        and task.get("vars", {}).get("installer_vm", {}).get("ha")
        == "{{ pdm_config.proxmox.vm.ha | default(false) }}"
        for play in pdm for task in play.get("tasks", [])
    ), "PDM's installer VM must carry the per-instance HA setting"


if __name__ == "__main__":
    module_tests()
    wiring_tests()
    print("Proxmox VM HA enable, disable, storage refusal, ownership, and Remove tests passed")
