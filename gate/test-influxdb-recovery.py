#!/usr/bin/env python3
"""Check InfluxDB's declared recovery path, its restore swap inputs and its restore hooks."""
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
DEPLOY = yaml.safe_load(
    (ROOT / "ansible/playbooks/apps/influxdb.yml").read_text(encoding="utf-8")
)
RESTORE = yaml.safe_load(
    (ROOT / "ansible/roles/influxdb/tasks/restore.yml").read_text(encoding="utf-8")
)
BACKUP = yaml.safe_load(
    (ROOT / "ansible/roles/influxdb/tasks/backup.yml").read_text(encoding="utf-8")
)
REUSE_GUEST = yaml.safe_load(
    (ROOT / "ansible/roles/influxdb/tasks/reuse-removed-guest.yml").read_text(encoding="utf-8")
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
        "subdomain": "influxdb",
        "proxy": "internal",
        "access": "internal",
        "identity": "catalog",
    }
    assert "#   subdomain: influxdb" in EXAMPLE
    assert "#   proxy: internal" in EXAMPLE
    assert "#   identity: catalog" in EXAMPLE

    wire_plays = [play for play in DEPLOY if play.get("name") == "InfluxDB | Wire"]
    assert len(wire_plays) == 1
    wire_play = wire_plays[0]
    assert DEPLOY.index(wire_play) > next(
        index for index, play in enumerate(DEPLOY)
        if play.get("name") == "InfluxDB | Register backend"
    )
    assert wire_play["vars"]["wiring_monitor_url"] == "https://{{ wiring_domain }}/health"
    assert wire_play["vars"]["wiring_access"] == "{{ _influxdb_host.app_config.routing.access | default('internal') }}"
    wire_tasks = wire_play["tasks"]
    record_index = next(
        index for index, task in enumerate(wire_tasks)
        if task.get("name") == "Record InfluxDB on its Proxmox guest"
    )
    isolation_index = next(
        index for index, task in enumerate(wire_tasks)
        if task.get("name") == "Skip external wiring while the restore target is isolated"
    )
    degradation_index = next(
        index for index, task in enumerate(wire_tasks)
        if task.get("name") == "Fail if anything could not be wired"
    )
    assert record_index < isolation_index
    assert wire_tasks[record_index]["ansible.builtin.include_tasks"] == (
        "../../tasks/proxmox/record-app-on-guest.yml"
    )
    assert wire_tasks[record_index]["vars"]["record_guest_kind"] == "native"
    assert wire_tasks[isolation_index]["ansible.builtin.meta"] == "end_play"
    assert isolation_index < degradation_index
    assert degradation_index == len(wire_tasks) - 1
    assert wire_tasks[degradation_index]["ansible.builtin.include_tasks"] == (
        "../../tasks/assert-no-degradations.yml"
    )
    for name in ["Wire reverse proxy", "Wire SSO", "Wire uptime monitoring", "Wire DNS"]:
        assert sum(task.get("name") == name for task in wire_tasks) == 1
        assert isolation_index < next(
            index for index, task in enumerate(wire_tasks) if task.get("name") == name
        ) < degradation_index
    assert wire_tasks.index(next(task for task in wire_tasks if task.get("name") == "Wire reverse proxy")) < wire_tasks.index(
        next(task for task in wire_tasks if task.get("name") == "Wire DNS")
    )

    provision_tasks = DEPLOY[0]["tasks"]
    reuse_index = next(
        index for index, task in enumerate(provision_tasks)
        if task.get("name") == "Find a removed InfluxDB guest that still has exact lab ownership"
    )
    placement_index = next(
        index for index, task in enumerate(provision_tasks)
        if task.get("name") == "Resolve InfluxDB guest placement and check existing type"
    )
    provision_index = next(
        index for index, task in enumerate(provision_tasks)
        if task.get("name") == "Provision a new InfluxDB backend guest"
    )
    assert reuse_index < placement_index < provision_index
    assert provision_tasks[provision_index]["ansible.builtin.include_tasks"] == (
        "../../tasks/database/provision-backend-guest.yml"
    )


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
    swap = swap_inputs("influxdb")
    assert swap["recovery_swap_paths"] == [{"path": "{{ app_config.app.data_path }}",
                                           "owner": "influxdb", "group": "influxdb", "mode": "0750"}]
    assert swap["recovery_swap_runtime"] == "native"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_stop_services"] == ["{{ _influxdb_restore_service_name }}"]
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
    assert upload["vars"]["pbs_client_runtime"] == "native"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ _influxdb_backup_service_name }}"]
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _influxdb_backup_data_path }}"}
    ]


def check_removed_guest_reuse():
    assert len(REUSE_GUEST) == 3
    assert "'_+lab' in app_config.proxmox.tags" in str(REUSE_GUEST[0])
    assert "['lxc', 'qemu']" in str(REUSE_GUEST[1])
    assert REUSE_GUEST[1]["ansible.builtin.set_fact"].get("_influxdb_removed_guest_hosts")
    assert "_influxdb_removed_guest_hosts | length <= 1" in str(REUSE_GUEST[2])

    def run_case(
        expected_hosts: list[str] | None,
        *,
        ambiguous: bool = False,
        configured_ip: str = "",
        configured_vmid: int = 0,
        vm_reuse: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        hosts = [
            {
                "name": "exact-owned-lxc",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123999,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "wrong-vmid",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123457,
                "proxmox_name": "other-instance",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.11/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "wrong-name",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123458,
                "proxmox_name": "other-app",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.12/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "running",
            },
            {
                "name": "influxdb",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123458,
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "wrong-ip",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123456,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "198.51.100.13/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "other-app-owner",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123461,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_sonarr"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "wrong-name-vm",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "qemu",
                "proxmox_vmid": 123462,
                "proxmox_name": "other-instance",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "not-lab-owned",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123463,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_-debian"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
        ]
        if vm_reuse:
            hosts[0]["proxmox_name"] = "other-instance"
            hosts[6]["name"] = "exact-owned-vm"
            hosts[6]["proxmox_name"] = "influxdb"
        if ambiguous:
            duplicate = copy.deepcopy(hosts[0])
            duplicate["name"] = "duplicate-exact-owned-lxc"
            hosts.append(duplicate)
        reuse_tasks = copy.deepcopy(REUSE_GUEST)
        for task in reuse_tasks:
            task.pop("no_log", None)
        tasks = [
            {"name": "Add owned inventory fixtures", "ansible.builtin.add_host": host,
             "changed_when": False}
            for host in hosts
        ] + reuse_tasks
        if expected_hosts is not None:
            tasks.append({
                "name": "Assert only the expected removed InfluxDB guest is selected",
                "ansible.builtin.assert": {
                    "that": f"_influxdb_removed_guest_hosts == {expected_hosts!r}"
                },
            })
        playbook = [{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "instance": "influxdb",
                "app_config": {"proxmox": {
                    "ip_address": configured_ip,
                    "tags": ["_+lab", "_-debian"],
                    "vmid": configured_vmid,
                }},
                "network_effective": {"cidr": "192.0.2.0/24"},
            },
            "tasks": tasks,
        }]
        with tempfile.TemporaryDirectory(prefix="homelab-influxdb-reuse.") as directory:
            fixture = Path(directory) / "reuse-guest.yml"
            fixture.write_text(yaml.safe_dump(playbook), encoding="utf-8")
            return subprocess.run(
                [find_ansible_playbook(), "-i", "localhost,", str(fixture)],
                cwd=ROOT,
                env=os.environ | {
                    "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                    "ANSIBLE_NOCOLOR": "1",
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                },
                text=True,
                capture_output=True,
                check=False,
            )

    exact = run_case(["exact-owned-lxc"])
    assert exact.returncode == 0, exact.stdout + exact.stderr
    exact_vm = run_case(["exact-owned-vm"], vm_reuse=True)
    assert exact_vm.returncode == 0, exact_vm.stdout + exact_vm.stderr
    configured = run_case(
        ["exact-owned-lxc"], configured_ip="192.0.2.10", configured_vmid=123999
    )
    assert configured.returncode == 0, configured.stdout + configured.stderr
    wrong_configured_ip = run_case([], configured_ip="192.0.2.99")
    assert wrong_configured_ip.returncode == 0, (
        wrong_configured_ip.stdout + wrong_configured_ip.stderr
    )
    wrong_configured_vmid = run_case([], configured_vmid=123456)
    assert wrong_configured_vmid.returncode == 0, (
        wrong_configured_vmid.stdout + wrong_configured_vmid.stderr
    )
    duplicate = run_case(None, ambiguous=True)
    assert duplicate.returncode != 0, duplicate.stdout + duplicate.stderr


def main():
    check_contract_and_wiring()
    check_backup_uses_shared_client()
    check_removed_guest_reuse()
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
