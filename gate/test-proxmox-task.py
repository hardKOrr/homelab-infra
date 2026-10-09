#!/usr/bin/env python3
"""Run the shared Proxmox waiter with real caller expressions and a local pvesh stub."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
waiter = repo / "ansible/tasks/proxmox/wait-for-task.yml"
failure = "A Proxmox task node and UPID are required before polling can begin."


def find_callers(value):
    if isinstance(value, list):
        for item in value:
            yield from find_callers(item)
    elif isinstance(value, dict):
        if str(value.get("ansible.builtin.include_tasks", "")).endswith("/wait-for-task.yml"):
            yield value["vars"]["proxmox_task_upid"]
        for item in value.values():
            yield from find_callers(item)


def main():
    callers = []
    for name in ("backup-guest.yml", "restore-guest.yml"):
        source = repo / "ansible/playbooks/maintenance" / name
        callers.extend(find_callers(yaml.safe_load(source.read_text())))
    registers = ("_bg_task", "_rg_pre_task", "_rg_stop_task", "_rg_restore_task", "_rg_start_task")
    assert len(callers) == 5, callers
    assert all(any(register in expression for expression in callers) for register in registers)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        log = root / "requests.jsonl"
        stub = root / "pvesh"
        stub.write_text(
            f"#!{ansible.parent / 'python'}\n"
            "import json, sys\n"
            f"with open({str(log)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "print(json.dumps({'status': 'stopped', 'exitstatus': 'OK'}))\n"
        )
        stub.chmod(0o755)
        tasks = []
        expected = []

        def include(expression, output):
            return {
                "ansible.builtin.include_tasks": str(waiter),
                "vars": {"proxmox_task_upid": expression,
                         **{register: {"stdout": output} for register in registers}},
            }

        # Each of the five production callers must poll exactly the same identity
        # for bare and JSON-quoted stdout, including pvesh's trailing newline.
        for index, expression in enumerate(callers):
            for operation in ("vzdump", "qmstop", "qmstart", "vzrestore", "vzstop", "vzstart"):
                upid = (f"UPID:<pve-node>:00000001:00000002:00000003:{operation}:"
                        f"<GUEST-vmid>:fixture{index}:")
                for output in (upid + "\n", json.dumps(upid) + "\n"):
                    tasks.append(include(expression, output))
                    expected.append(["get", f"/nodes/localhost/tasks/{upid}/status", "--output-format", "json"])

        # Run failures after successful calls to also detect stale normalized facts.
        upid = "UPID:<pve-node>:00000001:00000002:00000003:vzdump:<GUEST-vmid>:fixture:"
        invalid = ("", " \n\t", "not a task", "{}", json.dumps([upid]),
                   upid + "\n" + upid, upid + "\nnoise", json.dumps(upid + "\nnoise"),
                   '"' + upid, upid + '"', '""' + upid + '""',
                   "UPID:with space", "UPID:with\ttab", "UPID:with\rcarriage", '"UPID:with\nnewline"')
        for index, output in enumerate(invalid):
            tasks.append({
                "block": [include(callers[index % len(callers)], output),
                          {"ansible.builtin.fail": {"msg": "Invalid task output was accepted"}}],
                "rescue": [{"ansible.builtin.assert": {"that": [
                    f"ansible_failed_result.msg == {json.dumps(failure)}",
                    "ansible_failed_task.name == 'Proxmox | Require a task identity'",
                ]}}],
            })
        # Missing input must also reach the existing safety assertion.
        tasks.append({
            "block": [{"ansible.builtin.include_tasks": str(waiter)},
                      {"ansible.builtin.fail": {"msg": "Missing task output was accepted"}}],
            "rescue": [{"ansible.builtin.assert": {"that": [
                f"ansible_failed_result.msg == {json.dumps(failure)}",
            ]}}],
        })
        playbook = root / "waiter.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"proxmox_task_node": "localhost", "proxmox_task_timeout": 1,
                     "proxmox_task_poll": 1},
            "environment": {"PATH": str(root) + os.pathsep + os.environ["PATH"]},
            "tasks": tasks,
        }]))
        inventory = root / "inventory.ini"
        inventory.write_text("[proxmox_delegates]\nlocalhost ansible_connection=local\n")
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
        result = subprocess.run([str(ansible), "-i", str(inventory), str(playbook)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        assert requests == expected, requests
    print("Proxmox: all five callers accept bare/quoted UPIDs; invalid output fails before polling")


if __name__ == "__main__":
    main()
