#!/usr/bin/env python3
"""Exercise WireGuard's staged and running-interface recovery checks."""
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import run_hook, swap_inputs


def command_boundary(exit_code):
    def stub(tasks):
        for task in tasks:
            if "ansible.builtin.command" in task:
                task["ansible.builtin.command"] = {"argv": ["/bin/sh", "-c", f"exit {exit_code}"]}
                task.pop("until", None)
                task.pop("retries", None)
                task.pop("delay", None)
        return tasks
    return stub


def main():
    # Restore App publishes its resolved target as app_config on the guest, without
    # the controller-only restore_target_config fact. Exercise the actual guard.
    role = Path(__file__).resolve().parents[1] / "ansible/roles/wireguard/tasks"
    restore = yaml.safe_load((role / "restore.yml").read_text())
    def contract_only(_tasks):
        return [restore[0]]
    variables = {"app_config": {"hosting": {"method": "docker"}, "backup": {
        "enabled": True, "application_consistent": True}}, "homelabinfra_infra": {"backups": {}}}
    result = run_hook("wireguard", "staged", variables, contract_only)
    assert result.returncode == 0, result.stdout + result.stderr
    variables["app_config"]["hosting"]["method"] = "native"
    result = run_hook("wireguard", "staged", variables, contract_only)
    assert result.returncode != 0, result.stdout
    swap = swap_inputs("wireguard")
    assert swap["recovery_swap_runtime"] == "docker"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ wireguard_project_dir }}/config", "owner": "root", "group": "root", "mode": "0700"}]
    with tempfile.TemporaryDirectory() as directory:
        variables = {"recovery_swap_stage": directory, "app_config": {"app": {"interface": "wg0"}}}
        result = run_hook("wireguard", "staged", variables, command_boundary(0))
        assert result.returncode != 0, result.stdout
        config = Path(directory) / "data/wg_confs/wg0.conf"
        config.parent.mkdir(parents=True)
        config.write_text("[Interface]\nAddress = 10.66.66.1/24\n")
        for code in (0, 1):
            result = run_hook("wireguard", "staged", variables, command_boundary(code))
            assert (result.returncode == 0) == (code == 0), result.stdout + result.stderr
    for code in (0, 1):
        result = run_hook("wireguard", "verified", {"instance": "wireguard", "app_config": {
            "app": {"interface": "wg0"}}}, command_boundary(code))
        assert (result.returncode == 0) == (code == 0), result.stdout + result.stderr
    print("WireGuard recovery hooks: passed")


if __name__ == "__main__":
    main()
