#!/usr/bin/env python3
"""Check Deemix's ARL continuity, backup inputs and restore swap inputs."""

import copy
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"


def check_swap():
    """Deemix supplies its paths and health URL; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("deemix")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": "{{ app_config.app.puid }}",
                    "group": "{{ app_config.app.pgid }}", "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ deemix_project_dir }}"
    assert swap["recovery_swap_health_url"] == "http://localhost:{{ app_config.app.port }}"


def check_backup():
    """The shared PBS client stops, uploads and restarts; Deemix supplies only its inputs."""
    tasks = yaml.safe_load((repo / "ansible/roles/deemix/tasks/backup.yml").read_text())
    upload = next(t for t in tasks if t["name"] == "Deemix backup | Quiesce Deemix and archive its config to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "docker"
    assert upload["vars"]["pbs_client_compose_project"] == "{{ deemix_project_dir }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ instance }}"]
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ app_config.app.config_path }}"}]


def check_arl():
    deploy = yaml.safe_load((repo / "ansible/roles/deemix/tasks/main.yml").read_text())
    names = ["Resolve the app kind and ARL", "Check the existing Deezer ARL file",
             "Read the preserved Deezer ARL", "Adopt the preserved Deezer ARL for media wiring",
             "Seed the Deezer ARL", "Store and verify the ARL"]
    for original, configured, expected in [(None, "", ""), ("", "", ""),
                                           ("restored cookie", "", "restored cookie"),
                                           ("restored cookie", "rotated cookie", "rotated cookie")]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text('{"tracknameTemplate":"canary"}')
            cookie = root / ".arl"
            if original is not None:
                cookie.write_text(original)
                cookie.chmod(0o600)
            before = cookie.stat() if original is not None else None
            tasks = [copy.deepcopy(next(t for t in deploy if t["name"] == name)) for name in names]
            # Stub only Vaultwarden transport, retaining the production publication vars.
            publish = tasks[-1]
            publish.pop("ansible.builtin.include_tasks")
            publish["ansible.builtin.set_fact"] = {"published_arl": "{{ vault_item_fields.arl }}"}
            tasks[-2]["register"] = "seed_result"
            verify = {"ansible.builtin.assert": {"that": [
                "published_arl == expected_arl", "_dmx_arl == expected_arl"]}}
            path = root / "arl.yml"
            path.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"instance": "deemix", "expected_arl": expected,
                         "app_config": {"app": {"config_path": str(root), "arl": configured,
                                                "media_kind": "deemix", "port": 6595,
                                                "puid": os.getuid(), "pgid": os.getgid()}}},
                "tasks": tasks + [verify] + copy.deepcopy(tasks) + [verify, {
                    "ansible.builtin.assert": {"that": ["not seed_result.changed"]}}],
                "handlers": [{"name": "Restart deemix", "ansible.builtin.debug": {"msg": "restart"}}],
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                    text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
            assert cookie.read_text() == expected
            if original is not None and not configured:
                assert cookie.stat().st_mode == before.st_mode
                assert cookie.stat().st_mtime_ns == before.st_mtime_ns
            restarted = "RUNNING HANDLER [Restart deemix]" in result.stdout
            assert restarted == (original is None or bool(configured and configured != original))
            assert settings.read_text() == '{"tracknameTemplate":"canary"}'


def main():
    check_arl()
    check_backup()
    check_swap()
    print("Deemix: ARL continuity, backup inputs and restore swap inputs passed")


if __name__ == "__main__":
    main()
