#!/usr/bin/env python3
"""Exercise Frigate's CPU-only templates, backup inputs and restore-swap hook.

gate/test-restore-swap.py proves the swap and rollback; this test proves Frigate's staged
check and the inputs it passes.
"""

import copy
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import swap_inputs

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
backup_source = yaml.safe_load((repo / "ansible/roles/frigate/tasks/backup.yml").read_text())


def test_cpu_templates():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config_path = root / "frigate-config.yml"
        compose_path = root / "compose.yml"
        preserved_path = root / "saved-config.yml"
        preserved_path.write_text("ui_canary: preserved\n")
        play = [{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "vars": {
                "ansible_managed": "# generated",
                "instance": "frigate",
                "app_config": {"app": {
                    "detector": "cpu", "hwaccel_args": "", "mqtt": {}, "cameras": {},
                    "image": "frigate:test", "config_path": "/opt/frigate/config",
                    "recordings_path": "", "shm_size": "256m",
                    "port": 8971, "igpu_render_node": "", "coral_usb_device_path": "",
                }},
                "_frigate_rtsp_host_port": 8972,
                "_frigate_webrtc_host_port": 8973,
            },
            "tasks": [
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/config.yml.j2"),
                    "dest": str(config_path)}},
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/docker-compose.yml.j2"),
                    "dest": str(compose_path)}},
                {"ansible.builtin.template": {
                    "src": str(repo / "ansible/roles/frigate/templates/config.yml.j2"),
                    "dest": str(preserved_path), "force": False}},
            ],
        }]
        playbook = root / "render.yml"
        playbook.write_text(yaml.safe_dump(play))
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rendered_config = yaml.safe_load(config_path.read_text())
        rendered_compose = yaml.safe_load(compose_path.read_text())["services"]["frigate"]
        assert rendered_config["tls"] == {"enabled": False}
        assert rendered_config["detectors"] == {"cpu1": {"type": "cpu"}}
        assert rendered_config["cameras"] == {}
        assert "/dev/bus/usb" not in rendered_compose["volumes"]
        assert "/media/frigate" not in rendered_compose["volumes"]
        assert "devices" not in rendered_compose
        assert preserved_path.read_text() == "ui_canary: preserved\n"

    defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/frigate.yml").read_text())["frigate_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["backup"]["application_consistent"] is True
    role_tasks = yaml.safe_load((repo / "ansible/roles/frigate/tasks/main.yml").read_text())
    render = next(task for task in role_tasks if task.get("name") == "Frigate | Render the redacted Compose project and application config")
    assert render["ansible.builtin.template"]["force"] == "{{ item.force | default(true) | bool }}"
    config_template = next(item for item in render["loop"]
                           if item["dest"].endswith("/config.yml"))
    assert config_template["force"] is False


def test_public_health_endpoints():
    deploy_tasks = yaml.safe_load((repo / "ansible/roles/frigate/tasks/main.yml").read_text())

    def find_task(tasks, name):
        for task in tasks:
            if task.get("name") == name:
                return task
            for section in ["block", "rescue", "always"]:
                if section in task:
                    found = find_task(task[section], name)
                    if found:
                        return found
        raise AssertionError(f"Missing health task: {name}")

    task = find_task(deploy_tasks, "Frigate | Wait for the public API endpoint")
    uri = task["ansible.builtin.uri"]
    assert uri["url"].endswith("/api/auth/first_time_login")
    assert uri["status_code"] == 200
    assert uri["return_content"] is False
    assert task["no_log"] is False
    # The shared swap waits for a 200 without reading the body.
    assert swap_inputs("frigate")["recovery_swap_health_url"].endswith("/api/auth/first_time_login")


def test_deploy_preflight():
    playbook_source = yaml.safe_load((repo / "ansible/playbooks/apps/frigate.yml").read_text())
    guard = next(task for task in playbook_source[0]["pre_tasks"]
                 if task.get("name") == "Assert Frigate has an explicit detector and recording-storage contract")
    cases = [
        ({"cameras": {}, "recordings_path": "", "coral_usb_mapping": ""}, True),
        ({"cameras": {"front": {}}, "recordings_path": "", "coral_usb_mapping": ""}, False),
        ({"cameras": {}, "recordings_path": "/mnt/frigate-recordings", "coral_usb_mapping": ""}, False),
        ({"cameras": {}, "recordings_path": "", "coral_usb_mapping": "frigate-coral"}, False),
    ]
    for override, should_pass in cases:
        app = {
            "detector": "cpu", "recordings_path": "", "retention_days": 7,
            "gpu_device": "", "igpu_render_node": "", "coral_usb_mapping": "",
            "hwaccel_args": "", "cameras": {},
        } | override
        fixture = [{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"app_config": {"stack": "frigate", "app": app,
                                     "recording_storage": {}}},
            "tasks": [guard],
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight.yml"
            path.write_text(yaml.safe_dump(fixture))
            result = subprocess.run(
                [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                text=True, capture_output=True, check=False,
            )
        assert (result.returncode == 0) == should_pass, result.stdout + result.stderr


def check_restore_swap():
    swap = swap_inputs("frigate")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.config_path }}", "owner": 0, "group": 0, "mode": "0750"}
    assert swap["recovery_swap_stop_services"] == ["{{ instance }}"]
    assert swap["recovery_swap_compose_project"] == "{{ frigate_project_dir }}"
    assert swap["recovery_swap_health_retries"] == "{{ frigate_http_retries | default(60) }}"
    hook = yaml.safe_load((repo / "ansible/roles/frigate/tasks/restore-staged.yml").read_text())
    for case in ["missing-config", "missing-db", "integrity-failure", "complete"]:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            data = stage / "data"
            data.mkdir()
            if case != "missing-config":
                (data / "config.yml").write_text("detectors: {}\n")
            if case == "integrity-failure":
                (data / "frigate.db").write_text("not a sqlite database")
            elif case != "missing-db":
                sqlite3.connect(data / "frigate.db").close()
            playbook = stage / "hook.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"recovery_swap_stage": str(stage)}, "tasks": hook,
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                    text=True, capture_output=True, check=False)
            assert (result.returncode == 0) == (case == "complete"), case + result.stdout + result.stderr


def check_backup():
    """The shared PBS client stops, uploads and restarts; Frigate supplies its inputs and an
    integrity check that runs while it is stopped."""
    upload = next(t for t in backup_source
                  if t["name"] == "Frigate backup | Quiesce Frigate and archive its configuration and database to PBS")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "docker"
    assert upload["vars"]["pbs_client_compose_project"] == "{{ frigate_project_dir }}"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ instance }}"]
    assert upload["vars"]["pbs_client_quiesced_hook"] == "{{ role_path }}/tasks/backup-quiesced.yml"
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ app_config.app.config_path }}"}]
    hook = yaml.safe_load((repo / "ansible/roles/frigate/tasks/backup-quiesced.yml").read_text())
    for healthy in [True, False]:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            if healthy:
                sqlite3.connect(config / "frigate.db").close()
            else:
                (config / "frigate.db").write_text("not a sqlite database")
            playbook = config / "hook.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "vars": {"app_config": {"app": {"config_path": str(config)}}},
                "tasks": hook,
            }]))
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(playbook)],
                                    text=True, capture_output=True, check=False)
            assert (result.returncode == 0) == healthy, result.stdout + result.stderr


def main():
    test_cpu_templates()
    test_public_health_endpoints()
    test_deploy_preflight()
    check_restore_swap()
    check_backup()
    print("Frigate: CPU-only templates, backup inputs and integrity hook, restore swap inputs and staged check passed")


if __name__ == "__main__":
    main()
