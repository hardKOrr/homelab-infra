#!/usr/bin/env python3
"""Run the shared Proxmox waiter with real caller expressions and a local pvesh stub.

Checks: guest backup/restore task IDs, exact polling paths and invalid-output guards.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from remove_plan import load_playbook

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
            yield {
                "upid": value["vars"]["proxmox_task_upid"],
                "when": value.get("when"),
            }
        for item in value.values():
            yield from find_callers(item)


def find_named_tasks(value, name):
    if isinstance(value, list):
        for item in value:
            yield from find_named_tasks(item, name)
    elif isinstance(value, dict):
        if value.get("name") == name:
            yield value
        for item in value.values():
            yield from find_named_tasks(item, name)


def named_task(value, name):
    matches = list(find_named_tasks(value, name))
    assert len(matches) == 1, (name, matches)
    return matches[0]


def main():
    playbooks = {
        name: load_playbook(repo / "ansible/playbooks/maintenance" / name)
        for name in ("backup-guest.yml", "restore-guest.yml")
    }
    callers = [
        caller
        for playbook in playbooks.values()
        for caller in find_callers(playbook)
    ]
    registers = ("_bg_task", "_rg_pre_task", "_rg_stop_task", "_rg_restore_task", "_rg_start_task")
    assert len(callers) == 5, callers
    assert all(any(register in caller["upid"] for caller in callers) for register in registers)
    normalizers = {
        "_bg_task_upid": (
            "_bg_task_upids",
            named_task(playbooks["backup-guest.yml"], "Normalize the PBS backup task response"),
            named_task(playbooks["backup-guest.yml"], "Require at most one PBS backup task identity"),
            named_task(playbooks["backup-guest.yml"], "Select the PBS backup task identity"),
        ),
        "_rg_pre_task_upid": (
            "_rg_pre_task_upids",
            named_task(playbooks["restore-guest.yml"], "Normalize the independent target recovery task response"),
            named_task(playbooks["restore-guest.yml"], "Require at most one independent recovery task identity"),
            named_task(playbooks["restore-guest.yml"], "Select the independent target recovery task identity"),
        ),
        "_rg_restore_task_upid": (
            "_rg_restore_task_upids",
            named_task(playbooks["restore-guest.yml"], "Normalize the guest restore task response"),
            named_task(playbooks["restore-guest.yml"], "Require exactly one guest restore task identity"),
            named_task(playbooks["restore-guest.yml"], "Select the guest restore task identity"),
        ),
        "_rg_stop_task_upid": (
            "_rg_stop_task_upids",
            named_task(playbooks["restore-guest.yml"], "Normalize the existing target stop task response"),
            named_task(playbooks["restore-guest.yml"], "Require exactly one existing target stop task identity"),
            named_task(playbooks["restore-guest.yml"], "Select the existing target stop task identity"),
        ),
        "_rg_start_task_upid": (
            "_rg_start_task_upids",
            named_task(playbooks["restore-guest.yml"], "Normalize the existing target start task response"),
            named_task(playbooks["restore-guest.yml"], "Require exactly one existing target start task identity"),
            named_task(playbooks["restore-guest.yml"], "Select the existing target start task identity"),
        ),
    }
    normalizer_patterns = {
        normalize_task["ansible.builtin.set_fact"][list_fact]
        .split("regex_findall(", 1)[1]
        .split(")", 1)[0]
        for list_fact, normalize_task, _, _ in normalizers.values()
    }
    assert len(normalizer_patterns) == 1, normalizer_patterns

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

        def include(caller, output, normalized_upid=None):
            variables = {
                "proxmox_task_upid": caller["upid"],
                **{register: {"stdout": output} for register in registers},
            }
            fact = next((name for name in normalizers if name in caller["upid"]), None)
            if fact:
                variables[fact] = normalized_upid or output
            return [{
                "ansible.builtin.include_tasks": str(waiter),
                "vars": variables,
            }]

        # Exercise the production parser against a real multiline command response: the
        # UPID is one whole line between informational output, not the entire stdout.
        normalization_cases = []
        for fact, (list_fact, normalize_task, cardinality_task, select_task) in normalizers.items():
            register = {"_bg_task_upid": "_bg_task", "_rg_pre_task_upid": "_rg_pre_task",
                        "_rg_restore_task_upid": "_rg_restore_task",
                        "_rg_stop_task_upid": "_rg_stop_task",
                        "_rg_start_task_upid": "_rg_start_task"}[fact]
            upid = f"UPID:<pve-node>:00000001:00000002:00000003:vzrestore:<GUEST-vmid>:fixture:"
            output = "PVE operation accepted\n" + upid + "\nworker request complete\n"
            normalization_tasks = [
                {"ansible.builtin.set_fact": normalize_task["ansible.builtin.set_fact"]},
                {"ansible.builtin.assert": cardinality_task["ansible.builtin.assert"]},
                {"ansible.builtin.set_fact": select_task["ansible.builtin.set_fact"]},
                {"ansible.builtin.assert": {"that": [f"{fact} == {json.dumps(upid)}"]}},
            ]
            normalization_cases.append((register, output, normalization_tasks))
            # pvesh can wrap the task identity in JSON; every caller uses the same
            # extraction pattern and its cardinality assertion rejects ambiguity.
            normalization_cases.append((register, json.dumps({"data": upid}), normalization_tasks))

        # Each caller polls the same identity from bare and JSON-quoted stdout,
        # including pvesh's trailing newline.
        for index, caller in enumerate(callers):
            for operation in ("vzdump", "qmstop", "qmstart", "vzrestore", "vzstop", "vzstart"):
                upid = (f"UPID:<pve-node>:00000001:00000002:00000003:{operation}:"
                        f"<GUEST-vmid>:fixture{index}:")
                for output in (upid + "\n", json.dumps(upid) + "\n"):
                    tasks.extend(include(caller, output, normalized_upid=upid))
                    expected.append(["get", f"/nodes/localhost/tasks/{upid}/status", "--output-format", "json"])

        # Run failures after successful calls to also detect stale normalized facts.
        upid = "UPID:<pve-node>:00000001:00000002:00000003:vzdump:<GUEST-vmid>:fixture:"
        invalid = ("", " \n\t", "not a task", "{}", json.dumps([upid]),
                   upid + "\n" + upid, upid + "\nnoise", json.dumps(upid + "\nnoise"),
                   '"' + upid, upid + '"', '""' + upid + '""',
                   "UPID:with space", "UPID:with\ttab", "UPID:with\rcarriage", '"UPID:with\nnewline"')
        for index, output in enumerate(invalid):
            tasks.append({
                "block": [*include(callers[index % len(callers)], output),
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

        normalize_playbook = root / "normalize.yml"
        for register, output, normalization_tasks in normalization_cases:
            normalize_playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {register: {"stdout": output}},
                "tasks": normalization_tasks,
            }]))
            normalized = subprocess.run(
                [str(ansible), "-i", str(inventory), str(normalize_playbook)],
                env=env, text=True, capture_output=True, check=False,
            )
            assert normalized.returncode == 0, normalized.stdout + normalized.stderr

        restore_list_fact, normalize_restore, require_restore, _ = normalizers["_rg_restore_task_upid"]
        restore_upid = "UPID:<pve-node>:00000001:00000002:00000003:vzrestore:<GUEST-vmid>:restore:"
        ambiguous_upid = "UPID:<pve-node>:00000001:00000002:00000004:vzrestore:<GUEST-vmid>:ambiguous:"
        ambiguous_restore = root / "restore-response-ambiguous.yml"
        ambiguous_restore.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"_rg_restore_task": {"stdout": json.dumps({"data": [restore_upid, ambiguous_upid]})}},
            "tasks": [
                {"ansible.builtin.set_fact": normalize_restore["ansible.builtin.set_fact"]},
                {"block": [
                    {"name": "Require exactly one guest restore task identity",
                     "ansible.builtin.assert": require_restore["ansible.builtin.assert"]},
                    {"ansible.builtin.fail": {"msg": "Ambiguous restore response was accepted"}},
                ], "rescue": [{"ansible.builtin.assert": {"that": [
                    f"ansible_failed_result.msg == {json.dumps(require_restore['ansible.builtin.assert']['fail_msg'])}",
                    "ansible_failed_task.name == 'Require exactly one guest restore task identity'",
                    f"{restore_list_fact} | length == 2",
                ]}}]},
            ],
        }]))
        ambiguous = subprocess.run(
            [str(ansible), "-i", str(inventory), str(ambiguous_restore)],
            env=env, text=True, capture_output=True, check=False,
        )
        assert ambiguous.returncode == 0, ambiguous.stdout + ambiguous.stderr

        restore_response_selection = named_task(
            playbooks["restore-guest.yml"], "Select the guest restore task response")
        selector_playbook = root / "restore-selector.yml"
        for target_type, expected_stdout in (("qemu", "qemu-result"), ("lxc", "lxc-result")):
            selector_playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {
                    "_rg_execute": True,
                    "_rg_target_type": target_type,
                    "_rg_restore_vm_task": {"stdout": "qemu-result"},
                    "_rg_restore_lxc_task": {"stdout": "lxc-result"},
                },
                "tasks": [restore_response_selection, {
                    "ansible.builtin.assert": {"that": [
                        f"_rg_restore_task.stdout == {json.dumps(expected_stdout)}"]}
                }],
            }]))
            selected = subprocess.run(
                [str(ansible), "-i", str(inventory), str(selector_playbook)],
                env=env, text=True, capture_output=True, check=False,
            )
            assert selected.returncode == 0, selected.stdout + selected.stderr
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        assert requests == expected, requests
    print("Proxmox: all task normalizers extract plain and wrapped UPIDs; invalid task input fails before polling")


if __name__ == "__main__":
    main()
