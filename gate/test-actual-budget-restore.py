#!/usr/bin/env python3
"""Check Actual Budget's restore swap inputs and its password hook.

gate/test-restore-swap.py proves the swap and rollback. Actual Budget adds one step after the
restored server answers: reapplying the Vaultwarden-owned server password when one is set.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
role = repo / "ansible/roles/actual-budget"


def main():
    swap = swap_inputs("actual-budget", "restore-docker.yml")
    [path] = swap["recovery_swap_paths"]
    assert path["path"] == "{{ app_config.app.data_path }}"
    assert path["mode"] == "0750"
    for method, owner in [("docker", "1001"), ("native", "actual-budget")]:
        # Execute Ansible's rendering of the real swap inputs at the shared boundary.
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / "inputs.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {
                    "app_config": {"hosting": {"method": method},
                                   "app": {"data_path": "/opt/actual/data", "port": 5006,
                                           "service_name": "actual_server"}},
                    "actual_budget_native_user": "actual-budget",
                    "actual_budget_project_dir": "/opt/actual",
                    "role_path": str(role),
                },
                "tasks": [{"ansible.builtin.assert": {"that": [
                    "recovery_swap_runtime == '" + method + "'",
                    "recovery_swap_paths[0].owner | string == '" + owner + "'",
                    "recovery_swap_paths[0].group | string == '" + owner + "'",
                ]}, "vars": swap}],
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                                    text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
    assert swap["recovery_swap_stop_services"] == ["{{ app_config.app.service_name }}"]
    assert swap["recovery_swap_compose_project"] == "{{ actual_budget_project_dir }}"
    assert swap["recovery_swap_health_url"] == "http://localhost:{{ app_config.app.port }}"

    for password in ["", "vault password"]:
        tasks = yaml.safe_load((role / "tasks/restore-verified.yml").read_text())
        [reset] = tasks
        reset.pop("no_log")
        assert reset["ansible.builtin.include_tasks"] == "reset-password.yml"
        # The native utility is proven by test-actual-budget-password.py; record the call.
        del reset["ansible.builtin.include_tasks"]
        reset["ansible.builtin.set_fact"] = {"reset_with": "{{ actual_budget_reset_password }}"}
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / "play.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"app_config": {"app": {"server_password": password}}},
                "tasks": tasks + [{"ansible.builtin.assert": {"that": [
                    f"reset_with | default('') == '{password}'"]}}],
            }]))
            env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(play)],
                                    env=env, text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
    print("Actual Budget: restore swap inputs and the server password hook passed")


if __name__ == "__main__":
    main()
