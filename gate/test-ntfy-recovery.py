#!/usr/bin/env python3
"""Exercise Ntfy's PBS recovery contract and in-place rollback transaction."""

from pathlib import Path
import copy
import http.server
import json
import os
import subprocess
import tempfile
import threading

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"
BACKUP = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/backup.yml").read_text())
RESTORE = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/restore.yml").read_text())


def tasks_recursive(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks_recursive(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks_recursive(value.get(key, []))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def check_contract():
    defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/ntfy.yml").read_text())["ntfy_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2

    main = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/main.yml").read_text())
    install = task_named(main, "Install the PBS client repository when this lab declares app backups")
    assert install["ansible.builtin.include_tasks"] == "install-pbs-client.yml"
    assert "backups" in str(install["when"])
    assert task_named(main, "Install the PBS backup client")["ansible.builtin.apt"]["name"] == "proxmox-backup-client"

    pbs_client = yaml.safe_load((ROOT / "ansible/roles/ntfy/tasks/install-pbs-client.yml").read_text())
    names = [task.get("name") for task in tasks_recursive(pbs_client)]
    assert "Ntfy PBS client | Require a supported Debian release" in names
    assert "Ntfy PBS client | Configure the signed client repository" in names

    backup_tasks = list(tasks_recursive(BACKUP))
    upload = task_named(backup_tasks, "Ntfy backup | Upload auth and cache archives to PBS")
    argv = upload["ansible.builtin.command"]["argv"]
    assert "data.pxar:{{ _ntfy_backup_data_path }}" in argv
    assert "cache.pxar:{{ _ntfy_backup_cache_path }}" in argv
    assert upload.get("no_log") is True
    assert set(upload["environment"]) == {"PBS_REPOSITORY", "PBS_FINGERPRINT", "PBS_PASSWORD"}
    stop = task_named(backup_tasks, "Ntfy backup | Stop only Ntfy")
    restart = task_named(backup_tasks, "Ntfy backup | Restart only Ntfy when it was running")
    assert stop["ansible.builtin.systemd_service"]["state"] == "stopped"
    assert restart["ansible.builtin.systemd_service"]["state"] == "started"
    assert "_ntfy_backup_service_state.rc" in restart["when"]

    baseline = task_named(backup_tasks, "Ntfy backup | Read the existing recovery points")
    assert baseline["ansible.builtin.command"]["argv"][-2:] == ["--output-format", "json"]
    baseline_validation = task_named(
        backup_tasks, "Ntfy backup | Validate the baseline recovery point listing"
    )
    verify_new_snapshot = task_named(
        backup_tasks, "Ntfy backup | Confirm PBS recorded a new recovery point"
    )
    assert any("_ntfy_backup_snapshots_before.stdout" in condition
               and "from_json" in condition
               for condition in baseline_validation["ansible.builtin.assert"]["that"])
    assert any("_ntfy_backup_snapshots_before.stdout" in condition
               and "_ntfy_backup_snapshots.stdout" in condition
               and "length" in condition
               for condition in verify_new_snapshot["ansible.builtin.assert"]["that"])
    assert backup_tasks.index(baseline) < backup_tasks.index(upload) < backup_tasks.index(verify_new_snapshot)

    restore_tasks = list(tasks_recursive(RESTORE))
    source_guard = task_named(restore_tasks, "Ntfy restore | Require the source point to belong to its backup group")
    assert "_ntfy_restore_source_backup_id" in str(source_guard["ansible.builtin.assert"]["that"])
    prepoint_guard = task_named(restore_tasks, "Ntfy restore | Require the existing-target point to belong to its backup group")
    assert "_ntfy_restore_target_backup_id" in str(prepoint_guard["ansible.builtin.assert"]["that"])
    archive_guard = task_named(restore_tasks, "Ntfy restore | Require both application archives")
    assert "data.pxar" in str(archive_guard["ansible.builtin.assert"]["that"])
    assert "cache.pxar" in str(archive_guard["ansible.builtin.assert"]["that"])

    transaction = task_named(restore_tasks, "Ntfy restore | Replace and verify both state directories")
    block_names = [task.get("name") for task in transaction["block"]]
    for move in [
        "Ntfy restore | Move the original auth data aside on the same filesystem",
        "Ntfy restore | Move the original cache aside on the same filesystem",
    ]:
        item = task_named(transaction["block"], move)
        assert "mv" in item["ansible.builtin.command"]["argv"]
    assert block_names.index("Ntfy restore | Extract the selected auth database archive") < block_names.index(
        "Ntfy restore | Move the original auth data aside on the same filesystem")
    assert block_names.index("Ntfy restore | Extract the selected cache archive") < block_names.index(
        "Ntfy restore | Move the original cache aside on the same filesystem")
    assert block_names.index("Ntfy restore | Wait for the restored service") < block_names.index(
        "Ntfy restore | Mark the recovery complete")
    for task in transaction["block"]:
        if task.get("name", "").endswith("Create private staging directories beside each live path") or task.get("name", "").endswith(
            "Create private rollback directories beside each live path"):
            assert "item.path | dirname" in str(task["ansible.builtin.tempfile"]["path"])
    rescue_names = [task.get("name") for task in transaction["rescue"]]
    assert "Ntfy restore | Put the original auth data back" in rescue_names
    assert "Ntfy restore | Put the original cache back" in rescue_names
    assert rescue_names[-1] == "Ntfy restore | Report the failed restore"


def check_backup_snapshot_delta():
    backup_tasks = list(tasks_recursive(BACKUP))
    baseline_validation = copy.deepcopy(task_named(
        backup_tasks, "Ntfy backup | Validate the baseline recovery point listing"
    ))
    verify_new_snapshot = copy.deepcopy(task_named(
        backup_tasks, "Ntfy backup | Confirm PBS recorded a new recovery point"
    ))
    cases = [
        ("existing-snapshot-only", [{"backup-time": 100}], [{"backup-time": 100}], False),
        ("new-snapshot-added", [{"backup-time": 100}], [{"backup-time": 100}, {"backup-time": 200}], True),
        ("first-snapshot", [], [{"backup-time": 200}], True),
    ]
    for case, before, after, expected_success in cases:
        with tempfile.TemporaryDirectory(prefix="homelab-ntfy-backup-verify.") as directory:
            playbook = Path(directory) / "verify.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {
                    "_ntfy_backup_id": "ntfy",
                    "_ntfy_backup_snapshots_before": {"stdout": json.dumps(before)},
                    "_ntfy_backup_snapshots": {"stdout": json.dumps(after)},
                },
                "tasks": [baseline_validation, verify_new_snapshot],
            }]), encoding="utf-8")
            env = os.environ | {
                "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                "ANSIBLE_NOCOLOR": "1",
                "ANSIBLE_STDOUT_CALLBACK": "default",
            }
            result = subprocess.run(
                [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(playbook)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert (result.returncode == 0) == expected_success, (
                f"{case}: expected_success={expected_success}\n{result.stdout}{result.stderr}"
            )


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if self.server.failure else 200)
        self.end_headers()

    def log_message(self, *_args):
        pass


def fixture_tasks(tasks, root):
    """Stub PBS and systemd boundaries while running production restore file operations."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks", "")
        if include.endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "fixture",
                "k8s_pbs_fingerprint": "fixture",
                "k8s_pbs_password": "fixture",
            }
        elif include.endswith("fail-with-scrubbed-output.yml"):
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.fail"] = {
                "msg": "fixture PBS command failed: {{ scrub_failure_result.rc }}"
            }
            task["when"] = (
                "not (scrub_failure_result.skipped | default(false) | bool) and "
                "(scrub_failure_result.rc is not defined or "
                "scrub_failure_result.failed | default(false) | bool or "
                "scrub_failure_result.rc | int != 0)"
            )

        if task.get("ansible.builtin.stat", {}).get("path") == "/usr/bin/proxmox-backup-client":
            task.pop("ansible.builtin.stat")
            task.pop("register", None)
            task["ansible.builtin.set_fact"] = {
                "_ntfy_restore_client": {"stat": {"exists": True, "isreg": True}}
            }

        if "ansible.builtin.systemd_service" in task:
            service = task.pop("ansible.builtin.systemd_service")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/systemctl"), service["state"], service["name"]]
            }
            task["changed_when"] = True

        file_task = task.get("ansible.builtin.file", {})
        if file_task.get("owner") == "ntfy":
            file_task.pop("owner", None)
            file_task.pop("group", None)
        if "ansible.builtin.uri" in task:
            task["retries"] = 1
            task["delay"] = 0

        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


def check_restore(case, server):
    with tempfile.TemporaryDirectory(prefix="homelab-ntfy-recovery.") as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        data = root / "live" / "data"
        cache = root / "live" / "cache"
        data.mkdir(parents=True)
        cache.mkdir(parents=True)
        (data / "user.db").write_text("original auth canary\n")
        (data / "extra.txt").write_text("original data\n")
        (cache / "cache.db").write_text("original cached message\n")
        (cache / "extra.txt").write_text("original cache\n")
        before = {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
            for directory_path in [data, cache]
            for path in directory_path.rglob("*")
            if path.is_file()
        }
        data_inode, cache_inode = data.stat().st_ino, cache.stat().st_ino

        pbs = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
case = os.environ['NTFY_TEST_CASE']
args = sys.argv[1:]
if args[:2] == ['snapshot', 'list']:
    print('host/ntfy/2026-10-18T00:00:00Z')
elif args[:2] == ['snapshot', 'files']:
    print(json.dumps(['data.pxar', 'cache.pxar']))
elif args[0] == 'restore':
    archive, destination = args[2], Path(args[3])
    if case == 'extract-failure' and archive == 'data.pxar':
        raise SystemExit(7)
    if case == 'extract-cache-failure' and archive == 'cache.pxar':
        raise SystemExit(7)
    name = 'user.db' if archive == 'data.pxar' else 'cache.db'
    (destination / name).write_text('restored ' + name + '\n')
else:
    raise SystemExit('unexpected PBS command')
'''
        mv = r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
case = os.environ['NTFY_TEST_CASE']
src, dst = Path(sys.argv[-2]), Path(sys.argv[-1])
data, cache = Path(os.environ['NTFY_TEST_DATA']), Path(os.environ['NTFY_TEST_CACHE'])
root = Path(os.environ['NTFY_TEST_ROOT'])
with (root / 'mv.log').open('a') as log:
    log.write(f'{case} {src} -> {dst} marker={(root / "fail-rollback").exists()}\\n')
fail = (
    case == 'move-data' and src == data
    or case == 'move-cache' and src == cache
    or case == 'install-cache' and src.name == 'cache' and dst == cache
    or (root / 'fail-rollback').exists()
       and 'ntfy-pre-restore-data-' in str(src)
)
if fail:
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/mv', *sys.argv[1:]]).returncode)
'''
        chown = r'''#!/usr/bin/env python3
import os, sys
raise SystemExit(7 if os.environ['NTFY_TEST_CASE'] == 'ownership-failure' else 0)
'''
        systemctl = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
assert sys.argv[2:] == ['ntfy']
root = Path(os.environ['NTFY_TEST_ROOT'])
log = root / 'service.log'
states = log.read_text().splitlines() if log.exists() else []
state = sys.argv[1]
states.append(state)
log.write_text(''.join(item + '\n' for item in states))
if state == 'started' and os.environ['NTFY_TEST_CASE'] == 'start-failure' and states.count('started') == 1:
    raise SystemExit(7)
'''
        ntfy = r'''#!/usr/bin/env python3
import os, sys
if sys.argv[1:] != ['user', 'list']:
    raise SystemExit(2)
if os.environ['NTFY_TEST_CASE'] == 'rollback-failure':
    raise SystemExit(7)
'''
        for name, content in {
            "proxmox-backup-client": pbs,
            "mv": mv,
            "chown": chown,
            "systemctl": systemctl,
            "ntfy": ntfy,
        }.items():
            path = root / "bin" / name
            path.write_text(content)
            path.chmod(0o700)

        server.failure = case == "health-failure"
        if case == "rollback-failure":
            (root / "fail-rollback").touch()
        snapshot = "host/ntfy-other/2026-10-18T00:00:00Z" if case == "foreign-group" else "host/ntfy/2026-10-18T00:00:00Z"
        tasks = fixture_tasks(RESTORE, root)
        playbook = root / "restore.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "instance": "ntfy",
                "ntfy_service_name": "ntfy",
                "ntfy_auth_file": str(data / "user.db"),
                "ntfy_cache_file": str(cache / "cache.db"),
                "restore_backup_id": "ntfy",
                "restore_target_backup_id": "ntfy",
                "restore_snapshot": snapshot,
                "restore_pre_restore_point": "host/ntfy/2026-10-18T00:00:01Z",
                "restore_destination": "existing",
                "restore_overwrite": case != "plan",
                "homelabinfra_infra": {"backups": {}},
                "app_config": {
                    "app": {
                        "data_path": str(data),
                        "cache_path": str(cache),
                        "port": server.server_port,
                        "service_name": "ntfy",
                    },
                    "backup": {
                        "enabled": True,
                        "application_consistent": True,
                    },
                },
            },
            "tasks": tasks,
        }]), encoding="utf-8")
        env = os.environ | {
            "ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "NTFY_TEST_CASE": case,
            "NTFY_TEST_ROOT": str(root),
            "NTFY_TEST_DATA": str(data),
            "NTFY_TEST_CACHE": str(cache),
        }
        result = subprocess.run(
            [str(ANSIBLE), "-i", "localhost,", "-c", "local", str(playbook)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        success = case in ["plan", "success"]
        trace = (root / "mv.log").read_text() if (root / "mv.log").exists() else "<no mv calls>"
        assert (result.returncode == 0) == success, f"{case}: unexpected result\n{output}\nmv trace:\n{trace}"

        if case == "foreign-group":
            assert "must belong to" in output
            assert not (root / "service.log").exists(), "rejected point must not stop Ntfy"
        elif case == "plan":
            assert not (root / "service.log").exists(), "read-only plan must not stop Ntfy"

        if case == "rollback-failure":
            saved = list(data.parent.glob(".ntfy-pre-restore-data-*/state"))
            assert len(saved) == 1, output
            assert (saved[0] / "user.db").read_text() == "original auth canary\n"
            assert not data.exists(), "failed rollback must preserve the old tree for recovery"
        elif case == "success":
            assert (data / "user.db").read_text() == "restored user.db\n"
            assert (cache / "cache.db").read_text() == "restored cache.db\n"
            assert not (data / "extra.txt").exists()
            assert not (cache / "extra.txt").exists()
        elif case not in ["plan", "foreign-group"]:
            assert data.exists() and cache.exists(), output
            assert data.stat().st_ino == data_inode, "rollback must rename original auth data back"
            assert cache.stat().st_ino == cache_inode, "rollback must rename original cache back"
            after = {
                path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
                for directory_path in [data, cache]
                for path in directory_path.rglob("*")
                if path.is_file()
            }
            assert after == before, f"rollback changed original state\n{output}"

        leftovers = list(data.parent.glob(".ntfy-pre-restore-*") ) + list(cache.parent.glob(".ntfy-pre-restore-*"))
        if case not in ["rollback-failure"]:
            assert not leftovers, output
            assert not list(data.parent.glob(".ntfy-restore-*"))
            assert not list(cache.parent.glob(".ntfy-restore-*"))

        if case not in ["plan", "foreign-group"]:
            states = (root / "service.log").read_text().splitlines()
            assert states[0] == "stopped", (case, states)
            assert states[-1] == ("started" if case == "success" else "stopped" if case == "rollback-failure" else "started"), (case, states)


def main():
    check_contract()
    check_backup_snapshot_delta()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cases = [
            "plan", "foreign-group", "extract-failure", "extract-cache-failure",
            "move-data", "move-cache", "install-cache", "ownership-failure",
            "start-failure", "health-failure", "rollback-failure", "success",
        ]
        if os.environ.get("NTFY_TEST_ONLY"):
            cases = [os.environ["NTFY_TEST_ONLY"]]
        for case in cases:
            check_restore(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print("Ntfy: dual-archive PBS recovery, read-only plan, group guards and transactional rollback passed")


if __name__ == "__main__":
    main()
