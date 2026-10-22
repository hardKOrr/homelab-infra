#!/usr/bin/env python3
"""Run the guarded native LXC destroy path with local Proxmox command stubs."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
destroy_tasks = repo / "ansible/tasks/proxmox/destroy-owned-lxc.yml"


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bindir = root / "bin"
        bindir.mkdir()
        calls = root / "calls.jsonl"

        pvesh = bindir / "pvesh"
        pvesh.write_text(
            f"#!{ansible.parent / 'python'}\n"
            "import json, sys\n"
            f"with open({str(calls)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(['pvesh', *sys.argv[1:]]) + '\\n')\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['get', '/nodes/test-node/lxc/123/config']:\n"
            "    print(json.dumps({'hostname': 'postgresql', 'tags': '_+lab;_postgresql'}))\n"
            "elif args[:2] == ['get', '/nodes/test-node/qemu/456/config']:\n"
            "    print(json.dumps({'name': 'influxdb', 'tags': '_+lab;_influxdb'}))\n"
            "elif args[:2] == ['get', '/cluster/resources']:\n"
            "    print('[]')\n"
            "else:\n"
            "    raise SystemExit('unexpected pvesh request')\n"
        )
        pvesh.chmod(0o755)

        pct = bindir / "pct"
        pct.write_text(
            f"#!{ansible.parent / 'python'}\n"
            "import json, sys\n"
            f"with open({str(calls)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(['pct', *sys.argv[1:]]) + '\\n')\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['status']:\n"
            "    print('status: stopped')\n"
            "elif args[:1] != ['destroy']:\n"
            "    raise SystemExit('unexpected pct request')\n"
        )
        pct.chmod(0o755)

        inventory = root / "inventory.ini"
        inventory.write_text(
            "[localhost]\n"
            "localhost ansible_connection=local\n"
            "[proxmox_delegates]\n"
            "test-node ansible_connection=local ansible_host=wrong.invalid\n"
        )
        playbook = root / "destroy.yml"
        playbook.write_text(
            yaml.safe_dump(
                [
                    {
                        "hosts": "localhost",
                        "gather_facts": False,
                        "vars": {
                            "homelabinfra_config": {
                                "proxmox": {
                                    "node": "test-node",
                                    "api_host": "127.0.0.1",
                                    "nodes": {"test-node": "127.0.0.1"},
                                },
                                "ansible": {"ssh_user": "root"},
                            },
                            "destroy_lxc_instance": "postgresql",
                            "destroy_lxc_name": "postgresql",
                            "destroy_lxc_node": "test-node",
                            "destroy_lxc_vmid": 123,
                            "destroy_lxc_original_tags": ["_+lab", "_postgresql"],
                        },
                        "environment": {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]},
                        "tasks": [
                            {"ansible.builtin.include_tasks": str(destroy_tasks)},
                            {
                                "ansible.builtin.assert": {
                                    "that": [
                                        "hostvars['test-node'].ansible_host == '127.0.0.1'"
                                    ]
                                }
                            },
                        ],
                    }
                ]
            )
        )
        env = os.environ | {
            "ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg"),
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
        }
        result = subprocess.run(
            [str(ansible), "-i", str(inventory), str(playbook)],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        requests = [json.loads(line) for line in calls.read_text().splitlines()]
        assert [request[0] for request in requests] == [
            "pvesh", "pct", "pct", "pct", "pvesh"
        ], requests
        assert requests[0][2] == "/nodes/test-node/lxc/123/config", requests
        assert requests[3][1:] == ["destroy", "123"], requests
        assert requests[4][2:4] == ["/cluster/resources", "--type"], requests

        calls.unlink()
        stopped = root / "vm-stopped"
        qm = bindir / "qm"
        qm.write_text(
            f"#!{ansible.parent / 'python'}\n"
            "import json, sys\n"
            f"with open({str(calls)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(['qm', *sys.argv[1:]]) + '\\n')\n"
            f"stopped = {str(stopped)!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['status']:\n"
            "    print('status: stopped' if __import__('os').path.exists(stopped) else 'status: running')\n"
            "elif args[:1] == ['shutdown']:\n"
            "    open(stopped, 'a').close()\n"
            "elif args[:1] != ['destroy']:\n"
            "    raise SystemExit('unexpected qm request')\n"
        )
        qm.chmod(0o755)
        vm_playbook = root / "destroy-vm.yml"
        vm_playbook.write_text(
            yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {
                    "homelabinfra_config": {
                        "proxmox": {
                            "node": "test-node",
                            "api_host": "127.0.0.1",
                            "nodes": {"test-node": "127.0.0.1"},
                        },
                        "ansible": {"ssh_user": "root"},
                    },
                    "destroy_vm_instance": "influxdb",
                    "destroy_vm_name": "influxdb",
                    "destroy_vm_node": "test-node",
                    "destroy_vm_vmid": 456,
                    "destroy_vm_original_tags": ["_+lab", "_-debian", "_influxdb"],
                },
                "environment": {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]},
                "tasks": [{"ansible.builtin.include_tasks": str(repo / "ansible/tasks/proxmox/destroy-owned-vm.yml")}],
            }]),
            encoding="utf-8",
        )
        vm_result = subprocess.run(
            [str(ansible), "-i", str(inventory), str(vm_playbook)],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert vm_result.returncode == 0, vm_result.stdout + vm_result.stderr
        vm_requests = [json.loads(line) for line in calls.read_text().splitlines()]
        assert [request[0] for request in vm_requests] == [
            "pvesh", "qm", "qm", "qm", "qm", "pvesh"
        ], vm_requests
        assert vm_requests[0][2] == "/nodes/test-node/qemu/456/config", vm_requests
        assert vm_requests[2][1:] == ["shutdown", "456", "--timeout", "60"], vm_requests
        assert vm_requests[4][1:] == ["destroy", "456", "--purge", "1"], vm_requests
        assert vm_requests[5][2:4] == ["/cluster/resources", "--type"], vm_requests
    print("Owned LXC and VM destruction: registered-node delegation and ownership checks passed")


if __name__ == "__main__":
    main()
