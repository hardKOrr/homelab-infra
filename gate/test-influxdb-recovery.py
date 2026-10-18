#!/usr/bin/env python3
"""Check InfluxDB's declared recovery path and exercise its transactional restore."""
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


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = yaml.safe_load(
    (ROOT / "ansible/vars/app-defaults/influxdb.yml").read_text(encoding="utf-8")
)["influxdb_defaults"]
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
TRANSACTION = next(
    task for task in RESTORE
    if task.get("name") == "InfluxDB restore | Replace and validate the data directory"
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
    assert DEFAULTS["routing"]["proxy"] == "internal"
    assert DEFAULTS["routing"]["identity"] == "none"

    wire = next(play for play in DEPLOY if play.get("name") == "InfluxDB | Wire")
    wire_tasks = wire["tasks"]
    assert task_named(wire_tasks, "Wire reverse proxy")["when"]
    assert task_named(wire_tasks, "Register uptime monitor")["when"]
    assert task_named(wire_tasks, "Wire DNS")["when"]
    assert "https://{{ wiring_domain }}/health" == wire["vars"]["wiring_monitor_url"]
    assert task_named(wire_tasks, "Skip external wiring for an isolated restore target")


def all_tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from all_tasks(task[key])


def transform_transaction(tasks, fake_bin):
    """Replace machine service controls while retaining production file operations."""
    transformed = copy.deepcopy(tasks)
    for task in transformed:
        if task.get("name") == "InfluxDB restore | Report a failed data extraction":
            task["when"] = False
        if task.get("name") == "InfluxDB restore | Extract the selected archive to staging":
            task.pop("no_log", None)
        if "ansible.builtin.systemd_service" in task:
            service = task.pop("ansible.builtin.systemd_service")
            task["ansible.builtin.command"] = {
                "argv": [str(fake_bin / "systemctl"), service["state"], service["name"]]
            }
            task["changed_when"] = True
        if task.get("name") == "InfluxDB restore | Set the data ownership":
            task.pop("ansible.builtin.file")
            task["ansible.builtin.set_fact"] = {"fixture_ownership_checked": True}
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0
        for key in ("block", "rescue", "always"):
            if key in task:
                task[key] = transform_transaction(task[key], fake_bin)
    return transformed


class InfluxFixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        if self.path == "/api/v2/authorizations":
            live = self.server.live_path
            expected = (live / "influxd.bolt").read_text(encoding="utf-8")
            self.server.auth_headers.append(self.headers.get("Authorization", ""))
            status = 200 if self.headers.get("Authorization") == f"Token {expected}" else 401
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        pass


def write_executable(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def run_transaction(case: str, server: InfluxFixture):
    ansible_playbook = find_ansible_playbook()

    with tempfile.TemporaryDirectory(prefix="homelab-influxdb-recovery.") as directory:
        root = Path(directory)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        live = root / "var/lib/influxdb"
        live.mkdir(parents=True)
        (live / "influxd.bolt").write_text("original-token", encoding="utf-8")
        (live / "engine").mkdir()
        (live / "original-canary").write_text("preserved", encoding="utf-8")
        server.live_path = live
        server.auth_headers = []

        write_executable(
            fake_bin / "systemctl",
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if sys.argv[1:] == ['is-active', 'influxdb']:\n"
            "    print('active')\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(0)\n",
        )
        write_executable(
            fake_bin / "proxmox-backup-client",
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "if args[0] != 'restore' or args[2] != 'data.pxar':\n"
            "    raise SystemExit('unexpected PBS invocation')\n"
            "destination = Path(args[3])\n"
            "destination.mkdir(parents=True, exist_ok=True)\n"
            "(destination / 'influxd.bolt').write_text('restored-token')\n"
            "(destination / 'engine').mkdir()\n"
            "raise SystemExit(0)\n",
        )
        write_executable(
            fake_bin / "mv",
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "src, dst = sys.argv[1:3]\n"
            "if os.environ.get('INFLUX_TEST_CASE') == 'install-move-failure' and src.endswith('/restored'):\n"
            "    raise SystemExit(17)\n"
            "raise SystemExit(subprocess.run(['/bin/mv', src, dst]).returncode)\n",
        )

        transaction = transform_transaction([TRANSACTION], fake_bin)
        playbook = root / "restore.yml"
        playbook.write_text(
            yaml.safe_dump([{
                "hosts": "localhost",
                "connection": "local",
                "gather_facts": False,
                "vars": {
                    "instance": "influxdb",
                    "restore_target": "influxdb",
                    "restore_destination": "existing",
                    "restore_overwrite": True,
                    "restore_snapshot": "host/influxdb/2026-10-18T00:00:00Z",
                    "restore_pre_restore_point": "host/influxdb/2026-10-18T00:00:01Z",
                    "_influxdb_restore_data_path": str(live),
                    "_influxdb_restore_service_name": "influxdb",
                    "_influxdb_restore_snapshot": "host/influxdb/2026-10-18T00:00:00Z",
                    "_influxdb_restore_source_credentials": {
                        "operator_token": "wrong-token" if case == "auth-failure" else "restored-token"
                    },
                    "k8s_pbs_repository": "fixture@pbs!client@backup.example:store",
                    "k8s_pbs_fingerprint": "fixture-fingerprint",
                    "k8s_pbs_password": "fixture-password",
                    "_influxdb_restore_target_credentials": {
                        "operator_token": "original-token"
                    },
                    "_application_restore_complete": False,
                    "_influxdb_restore_previous_moved": False,
                    "_influxdb_restore_new_installed": False,
                    "_influxdb_restore_rollback_complete": False,
                    "app_config": {"app": {"port": server.server_port}},
                },
                "tasks": transaction,
            }]),
            encoding="utf-8",
        )
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "INFLUX_TEST_CASE": case,
        }
        result = subprocess.run(
            [ansible_playbook, "-i", "localhost,", str(playbook)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        if case == "success":
            assert result.returncode == 0, result.stdout + result.stderr
            assert (live / "influxd.bolt").read_text(encoding="utf-8") == "restored-token"
            assert not (live / "original-canary").exists()
            assert server.auth_headers == ["Token restored-token"]
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert "original data directory was restored" in result.stdout + result.stderr
            assert (live / "influxd.bolt").read_text(encoding="utf-8") == "original-token"
            assert (live / "original-canary").read_text(encoding="utf-8") == "preserved"
            assert server.auth_headers[-1] == "Token original-token"

        assert not list(live.parent.glob(".influxdb-restore-stage-*"))
        assert not list(live.parent.glob(".influxdb-pre-restore-*"))


def check_restore_transaction():
    block_names = [task.get("name") for task in TRANSACTION["block"]]
    rescue_names = [task.get("name") for task in TRANSACTION["rescue"]]
    assert "InfluxDB restore | Move the original data aside on the same filesystem" in block_names
    assert "InfluxDB restore | Move the staged data into place" in block_names
    assert "InfluxDB restore | Put the original data back" in rescue_names
    assert "InfluxDB restore | Report failure after restoring the original data" == rescue_names[-1]
    move_original = task_named(TRANSACTION["block"],
                               "InfluxDB restore | Move the original data aside on the same filesystem")
    assert "_influxdb_restore_data_path | dirname" in str(TRANSACTION["block"])
    assert "_influxdb_restore_dirs.results[1].path }}/state" in str(
        move_original["ansible.builtin.command"]["argv"]
    )
    assert block_names.index("InfluxDB restore | Wait for the restored service") < block_names.index(
        "InfluxDB restore | Remove the previous target data after verification"
    )
    assert "InfluxDB restore | Verify the restored operator token" in block_names


def check_backup_snapshot_identity():
    task_names = (
        "InfluxDB backup | Validate the recovery points after upload",
        "InfluxDB backup | Read recovery point timestamps",
        "InfluxDB backup | Find the newly added recovery point timestamp",
        "InfluxDB backup | Require one new recovery point",
        "InfluxDB backup | Format the verified recovery point",
    )
    resolution_tasks = copy.deepcopy([
        task_named(list(all_tasks(BACKUP)), name) for name in task_names
    ])
    for task in resolution_tasks:
        task.pop("no_log", None)
    assert all("ansible.builtin.command" not in task for task in resolution_tasks)
    assert "| difference(" in str(resolution_tasks[2])
    assert "strftime(" in str(resolution_tasks[-1])
    assert "utc=true" in str(resolution_tasks[-1])

    before = [{"backup-time": 1792371000}, {"backup-time": 1792371500}]
    added = 1792371600
    after = before + [{"backup-time": added}]
    expected = (
        "host/fixture/"
        + datetime.datetime.fromtimestamp(added, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    playbook = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "_influxdb_backup_snapshots_before": {"stdout": json.dumps(before)},
            "_influxdb_backup_snapshots_after": {"stdout": json.dumps(after)},
            "_influxdb_backup_id": "fixture",
        },
        "tasks": resolution_tasks + [{
            "name": "Assert fixture snapshot identity",
            "ansible.builtin.assert": {
                "that": "_influxdb_backup_recovery_point == expected_recovery_point"
            },
            "vars": {"expected_recovery_point": expected},
        }],
    }]
    with tempfile.TemporaryDirectory(prefix="homelab-influxdb-backup-point.") as directory:
        fixture = Path(directory) / "backup-point.yml"
        fixture.write_text(yaml.safe_dump(playbook), encoding="utf-8")
        result = subprocess.run(
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
    assert result.returncode == 0, result.stdout + result.stderr


def check_removed_guest_reuse():
    assert len(REUSE_GUEST) == 3
    assert "'_+lab' in homelabinfra_config.proxmox.lxc.tags" in str(REUSE_GUEST[0])
    assert REUSE_GUEST[1]["ansible.builtin.set_fact"].get("_influxdb_removed_guest_hosts")
    assert "_influxdb_removed_guest_hosts | length <= 1" in str(REUSE_GUEST[2])

    def run_case(ambiguous: bool) -> subprocess.CompletedProcess[str]:
        hosts = [
            {
                "name": "exact-owned-lxc",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123456,
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
                "proxmox_name": "influxdb",
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
                "name": "wrong-ip",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123456,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.13/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "other-app-owner",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "lxc",
                "proxmox_vmid": 123459,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_other-app"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
            {
                "name": "wrong-guest-type",
                "groups": ["lab_managed"],
                "proxmox_vmtype": "qemu",
                "proxmox_vmid": 123460,
                "proxmox_name": "influxdb",
                "proxmox_tags_parsed": ["_+lab", "_-debian"],
                "proxmox_net0": {"ip": "192.0.2.10/24"},
                "proxmox_node": "fixture-node",
                "proxmox_status": "stopped",
            },
        ]
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
        if not ambiguous:
            tasks.append({
                "name": "Assert only the exact owned LXC is selected",
                "ansible.builtin.assert": {
                    "that": "_influxdb_removed_guest_hosts == ['exact-owned-lxc']"
                },
            })
        playbook = [{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "instance": "influxdb",
                "homelabinfra_config": {"proxmox": {"lxc": {
                    "hostname": "influxdb",
                    "vmid": 123456,
                    "ip_address": "192.0.2.10",
                    "tags": ["_+lab", "_-debian", "_influxdb"],
                }}},
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

    exact = run_case(ambiguous=False)
    assert exact.returncode == 0, exact.stdout + exact.stderr
    duplicate = run_case(ambiguous=True)
    assert duplicate.returncode != 0, duplicate.stdout + duplicate.stderr


def main():
    check_contract_and_wiring()
    check_restore_transaction()
    check_backup_snapshot_identity()
    check_removed_guest_reuse()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), InfluxFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_transaction("success", server)
        run_transaction("install-move-failure", server)
        run_transaction("auth-failure", server)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    print("InfluxDB: recovery declaration, unique wiring, backup identity, archive restore and post-move rollback passed")


if __name__ == "__main__":
    main()
