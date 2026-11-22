#!/usr/bin/env python3
"""Check InfluxDB's declared recovery path, its restore swap inputs and its restore hooks.

Checks: native data archive, restore credential checks, and the restore swap inputs with the
Bolt and engine staged hook, the operator-token check, and a new target's Vaultwarden
credentials written on success and put back on rollback.
"""
from __future__ import annotations

import copy
import datetime
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import yaml

from restore_swap_inputs import swap_inputs


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = yaml.safe_load(
    (ROOT / "ansible/vars/app-defaults/influxdb.yml").read_text(encoding="utf-8")
)["influxdb_defaults"]
EXAMPLE = (ROOT / "config.example/apps/influxdb.example.yml").read_text(encoding="utf-8")
RESTORE = yaml.safe_load(
    (ROOT / "ansible/roles/influxdb/tasks/restore-guest.yml").read_text(encoding="utf-8")
)
BACKUP = yaml.safe_load(
    (ROOT / "ansible/roles/influxdb/tasks/backup.yml").read_text(encoding="utf-8")
)
def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def find_ansible_playbook():
    ansible_playbook = (
        os.environ.get("GATE_ANSIBLE_PLAYBOOK")
        or shutil.which("ansible-playbook")
        or str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook")
    )
    assert Path(ansible_playbook).is_file(), (
        "ansible-playbook is required for the InfluxDB recovery gate"
    )
    return ansible_playbook


def check_contract_and_wiring():
    assert DEFAULTS["recovery"]["methods"] == ["native"]
    assert DEFAULTS["recovery"]["native"] == {
        "backup_playbook": "backup-app.yml",
        "restore_playbook": "restore-app.yml",
    }
    assert DEFAULTS["backup"]["enabled"] is True
    assert DEFAULTS["backup"]["application_consistent"] is True
    assert DEFAULTS["app"]["data_path"] == "/var/lib/influxdb"
    assert DEFAULTS["routing"] == {
        "monitor_path": "/health",
        "subdomain": "influxdb",
        "proxy": "internal",
        "access": "internal",
        "identity": "catalog",
    }
    assert "#   subdomain: influxdb" in EXAMPLE
    assert "#   proxy: internal" in EXAMPLE
    assert "#   identity: catalog" in EXAMPLE

    # Deploy is the shared deploy.yml: the database-backend hooks place a native guest and
    # record the registry entry; a removed guest with the exact name is reused by the shared
    # tasks/proxmox/resolve-instance-guest.yml (gate/test-instance-guest-reuse.py).
    assert DEFAULTS["routing"]["monitor_path"] == "/health"
    assert "tasks/database/deploy-prepare.yml" in (
        ROOT / "ansible/roles/influxdb/tasks/deploy-prepare.yml").read_text(encoding="utf-8")
    assert "tasks/database/deploy-record.yml" in (
        ROOT / "ansible/roles/influxdb/tasks/deploy-record.yml").read_text(encoding="utf-8")


def all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from all_tasks(task[key])


class InfluxFixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api/v2/authorizations"
        self.send_response(200 if self.headers.get("Authorization") == "Token restored-token" else 401)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


def run_hook(hook, stage, port, overrides=None):
    tasks = yaml.safe_load((ROOT / f"ansible/roles/influxdb/tasks/restore-{hook}.yml").read_text(encoding="utf-8"))
    for task in tasks:
        task.pop("no_log", None)
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("upsert-item.yml"):
            # Vaultwarden is the external boundary; record which credentials would be written.
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.copy"] = {"content": "{{ vault_item_fields.operator_token }}",
                                            "dest": str(stage / f"vault-{hook}")}
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "hook.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"instance": "influxdb", "recovery_swap_stage": str(stage),
                     "restore_destination": "existing",
                     "_influxdb_restore_source_credentials": {
                         "admin_user": "admin", "admin_password": "source", "operator_token": "restored-token",
                         "organization": "lab", "bucket": "lab"},
                     "_influxdb_restore_target_credentials": {
                         "admin_user": "admin", "admin_password": "target", "operator_token": "original-token",
                         "organization": "lab", "bucket": "lab"},
                     "app_config": {"app": {"port": port}}} | (overrides or {}),
            "tasks": tasks,
        }]), encoding="utf-8")
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([find_ansible_playbook(), "-i", "localhost,", "-c", "local", str(playbook)],
                              env=env, text=True, capture_output=True, check=False)


def check_restore_swap(server):
    """InfluxDB supplies its path, owner and checks; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("influxdb", "restore-guest.yml")
    assert swap["recovery_swap_paths"] == [{"path": "{{ influxdb_data_path }}",
                                           "owner": "{{ influxdb_owner }}", "group": "{{ influxdb_owner }}",
                                           "mode": "{{ '0700' if influxdb_docker | bool else '0750' }}"}]
    assert swap["recovery_swap_runtime"] == "{{ 'docker' if influxdb_docker | bool else 'native' }}"
    assert swap["recovery_swap_compose_project"] == "{{ influxdb_project_dir if influxdb_docker | bool else '' }}"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert "_influxdb_restore_service_name" in swap["recovery_swap_stop_services"]
    assert "influxdb_compose_service" in swap["recovery_swap_stop_services"]
    assert swap["recovery_swap_health_url"].endswith("/health")
    names = [task.get("name") for task in RESTORE]
    assert names.index("InfluxDB restore | Refuse an inactive target") < names.index(
        "InfluxDB restore | Preview or replace the data directory")

    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        (stage / "data").mkdir()
        assert run_hook("staged", stage, server.server_port).returncode != 0, "empty point"
        (stage / "data/influxd.bolt").write_text("bolt", encoding="utf-8")
        assert run_hook("staged", stage, server.server_port).returncode != 0, "no engine"
        (stage / "data/engine").write_text("not a directory", encoding="utf-8")
        assert run_hook("staged", stage, server.server_port).returncode != 0, "engine not a directory"
        (stage / "data/engine").unlink()
        (stage / "data/engine").mkdir()
        result = run_hook("staged", stage, server.server_port)
        assert result.returncode == 0, result.stdout + result.stderr

        wrong = {"_influxdb_restore_source_credentials": {"operator_token": "wrong-token"}}
        assert run_hook("verified", stage, server.server_port, wrong).returncode != 0, "wrong token"
        result = run_hook("verified", stage, server.server_port)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (stage / "vault-verified").exists(), "an existing target keeps its credentials"
        result = run_hook("verified", stage, server.server_port, {"restore_destination": "new"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert (stage / "vault-verified").read_text(encoding="utf-8") == "restored-token"
        result = run_hook("rollback", stage, server.server_port, {"restore_destination": "new"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert (stage / "vault-rollback").read_text(encoding="utf-8") == "original-token"


def check_backup_uses_shared_client():
    upload = task_named(list(all_tasks(BACKUP)), "InfluxDB backup | Upload the quiesced data directory")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "{{ 'docker' if influxdb_docker | bool else 'native' }}"
    assert "_influxdb_backup_service_name" in upload["vars"]["pbs_client_stop_services"]
    assert "influxdb_compose_service" in upload["vars"]["pbs_client_stop_services"]
    assert upload["vars"]["pbs_client_compose_project"] == "{{ influxdb_project_dir if influxdb_docker | bool else '' }}"
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _influxdb_backup_data_path }}"}
    ]


def main():
    check_contract_and_wiring()
    check_backup_uses_shared_client()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), InfluxFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        check_restore_swap(server)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    print("InfluxDB: native recovery, internal HTTPS wiring, shared PBS client, restore swap inputs and restore hooks passed")


if __name__ == "__main__":
    main()
