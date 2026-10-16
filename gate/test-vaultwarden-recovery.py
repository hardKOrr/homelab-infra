#!/usr/bin/env python3
"""Guard Vaultwarden's native recovery and additional-instance isolation contract."""

from pathlib import Path
import copy
import http.server
import importlib.util
import os
import subprocess
import tempfile
import threading

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_PLAYBOOK = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"


def tasks_recursive(value):
    if isinstance(value, list):
        for item in value:
            yield from tasks_recursive(item)
    elif isinstance(value, dict):
        if "name" in value:
            yield value
        for key in ["pre_tasks", "tasks", "block", "rescue", "always"]:
            yield from tasks_recursive(value.get(key, []))


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def check_contract_and_dispatch():
    defaults = read_yaml(ROOT / "ansible/vars/app-defaults/vaultwarden.yml")["vaultwarden_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2

    backup = read_yaml(ROOT / "ansible/playbooks/maintenance/backup-app.yml")
    backup_tasks = list(tasks_recursive(backup))
    backup_assert = task_named(backup_tasks, "Assert this app has an application backup")
    assert "native" in str(backup_assert["ansible.builtin.assert"]["that"])
    target = task_named(backup_tasks, "Resolve the Docker or native-LXC application target")
    assert "native" in str(target["when"])
    dispatch = task_named(backup_tasks, "Run the application-owned backup")
    assert "native" in str(dispatch["when"])

    restore = read_yaml(ROOT / "ansible/playbooks/maintenance/restore-app.yml")
    restore_tasks = list(tasks_recursive(restore))
    restore_assert = task_named(restore_tasks, "Assert this app has a restore path")
    assert "native" in str(restore_assert["ansible.builtin.assert"]["that"])
    target = task_named(restore_tasks, "Resolve the Docker or native-LXC target")
    assert "native" in str(target["when"])
    dispatch = task_named(restore_tasks, "Restore the application-owned archive")
    assert "native" in str(dispatch["when"])
    assert "restore_target_backup_id" in dispatch["vars"]

    deploy = read_yaml(ROOT / "ansible/playbooks/apps/vaultwarden.yml")
    deploy_tasks = list(tasks_recursive(deploy))
    second_play = deploy[1]
    resolver = task_named(tasks_recursive(second_play), "Resolve the estate domain for this Vaultwarden instance")
    assert resolver["ansible.builtin.import_tasks"] == "../../tasks/resolve-estate.yml"
    assert "app_config.routing.estate" in resolver["vars"]["resolve_estate_name"]
    record = task_named(deploy_tasks, "Record Vaultwarden facts")
    assert record["when"] == "instance == 'vaultwarden'", (
        "a family instance must not replace the platform Vaultwarden registry entry")

    registry_spec = importlib.util.spec_from_file_location(
        "registry_forget", ROOT / "ansible/scripts/registry-forget.py")
    assert registry_spec and registry_spec.loader
    registry_module = importlib.util.module_from_spec(registry_spec)
    registry_spec.loader.exec_module(registry_module)
    registry = {
        "vaultwarden": {"host": "http://root-vault.invalid", "port": 80},
        "apps": {"vaultwarden-family": {"host": "http://family.invalid", "port": 80}},
    }
    assert registry_module.prune(registry, "vaultwarden-family") == {
        "vaultwarden": {"host": "http://root-vault.invalid", "port": 80}}

    pbs_client = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/install-pbs-client.yml")
    pbs_tasks = list(tasks_recursive(pbs_client))
    supported = task_named(pbs_tasks, "Vaultwarden PBS client | Require a supported Debian release")
    assert supported["ansible.builtin.assert"]["that"] == [
        "ansible_distribution == 'Debian'",
        "ansible_distribution_release in ['bookworm', 'trixie']",
    ]
    bookworm_key = task_named(pbs_tasks, "Vaultwarden PBS client | Select the Debian Bookworm signing key")
    assert bookworm_key["ansible.builtin.set_fact"]["_vaultwarden_pbs_keyring"].endswith(
        "proxmox-release-bookworm.gpg")
    trixie_key = task_named(pbs_tasks, "Vaultwarden PBS client | Select the Debian Trixie signing key")
    assert trixie_key["ansible.builtin.set_fact"]["_vaultwarden_pbs_key_url"].endswith(
        "proxmox-archive-keyring-trixie.gpg")
    repository = task_named(pbs_tasks, "Vaultwarden PBS client | Configure the signed client repository")
    assert "{{ ansible_distribution_release }}" in repository["ansible.builtin.copy"]["content"]
    assert "{{ _vaultwarden_pbs_keyring }}" in repository["ansible.builtin.copy"]["content"]


def check_backup_role():
    tasks = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/backup.yml")
    names = [task.get("name", "") for task in tasks_recursive(tasks)]
    assert "Vaultwarden backup | Resolve the PBS client target" in names
    assert "Vaultwarden backup | Stop only this instance" in names
    upload = task_named(tasks_recursive(tasks), "Vaultwarden backup | Upload the complete data directory to PBS")
    upload_argv = upload["ansible.builtin.command"]["argv"]
    assert "data.pxar:{{ _vw_backup_data_path }}" in upload_argv
    assert "--backup-id" in upload_argv and upload.get("no_log") is True
    assert set(upload["environment"]) == {"PBS_REPOSITORY", "PBS_FINGERPRINT", "PBS_PASSWORD"}

    assert any(task.get("name") == "Vaultwarden backup | Check the SQLite database"
               for task in tasks_recursive(tasks))
    verify = task_named(tasks_recursive(tasks), "Vaultwarden backup | Confirm the recovery group contains a snapshot")
    assert "_vw_backup_id in _vw_backup_snapshots.stdout" in verify["ansible.builtin.assert"]["that"]
    prune = task_named(tasks_recursive(tasks), "Vaultwarden backup | Retain the two newest points and daily history")
    assert prune["ansible.builtin.command"]["argv"][-4:] == [
        "--keep-last", "2", "--keep-daily", "{{ app_config.backup.retention_days | default(14) | int }}"
    ]
    backup_play = next(play for play in read_yaml(ROOT / "ansible/playbooks/maintenance/backup-app.yml")
                       if play.get("name") == "Backup App | Run the application-owned backup")
    assert backup_play["tasks"][-1]["name"] == "Fail if application backup degraded"


def check_restore_transaction():
    tasks = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/restore.yml")
    all_tasks = list(tasks_recursive(tasks))
    assert "Vaultwarden restore | Validate the selected source group" in [task.get("name") for task in all_tasks]
    assert "Vaultwarden restore | Validate the independent pre-restore group" in [task.get("name") for task in all_tasks]
    source_archive = task_named(all_tasks, "Vaultwarden restore | Verify the selected archive")
    assert source_archive["ansible.builtin.command"]["argv"][1:3] == ["snapshot", "files"]
    assert source_archive.get("no_log") is True

    replace = task_named(all_tasks, "Vaultwarden restore | Replace and verify the data directory")
    block = replace["block"]
    names = [task.get("name") for task in block]
    extract_i = names.index("Vaultwarden restore | Extract the selected PBS archive")
    db_i = names.index("Vaultwarden restore | Check the staged SQLite database")
    move_i = names.index("Vaultwarden restore | Move the original data aside on the same filesystem")
    install_i = names.index("Vaultwarden restore | Move the staged data into place")
    health_i = names.index("Vaultwarden restore | Wait for the restored API")
    assert extract_i < db_i < move_i < install_i < health_i
    extract = block[extract_i]
    assert extract.get("no_log") is True
    assert extract["ansible.builtin.command"]["argv"][1:4] == [
        "restore", "{{ _vw_restore_snapshot }}", "data.pxar"
    ]

    rescue_names = [task.get("name") for task in replace["rescue"]]
    assert rescue_names.index("Vaultwarden restore | Remove the failed replacement data") < rescue_names.index(
        "Vaultwarden restore | Put the original data back")
    restart = task_named(replace["rescue"], "Vaultwarden restore | Restart the service after a successful rollback")
    assert restart["ansible.builtin.systemd_service"]["state"] == "started"
    assert "_vw_restore_rollback_complete" in str(restart["when"])
    assert rescue_names.index(restart["name"]) < rescue_names.index("Vaultwarden restore | Report the failed restore")
    assert rescue_names[-1] == "Vaultwarden restore | Report the failed restore"
    cleanup = task_named(replace["always"], "Vaultwarden restore | Remove rollback data after success or rollback")
    assert "_vw_restore_old_data_moved" in str(cleanup["when"])
    stage_cleanup = task_named(replace["always"], "Vaultwarden restore | Remove private staging data")
    assert "_vw_restore_stage.path" in stage_cleanup["ansible.builtin.file"]["path"]


def fixture_tasks(tasks, root):
    """Stub PBS and systemd boundaries while running the production restore operations."""
    tasks = copy.deepcopy(tasks)
    for task in tasks:
        if task.get("ansible.builtin.include_tasks", "").endswith("resolve-pbs-target.yml"):
            del task["ansible.builtin.include_tasks"]
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "k8s_pbs_available": True,
                "k8s_pbs_repository": "fixture",
                "k8s_pbs_fingerprint": "fixture",
                "k8s_pbs_password": "fixture",
            }
        elif task.get("ansible.builtin.include_tasks", "").endswith("fail-with-scrubbed-output.yml"):
            del task["ansible.builtin.include_tasks"]
            task["ansible.builtin.fail"] = {
                "msg": "fixture reports a scrubbed PBS command failure: {{ scrub_failure_result.rc }} {{ scrub_failure_result.stderr }}"
            }
            task["when"] = (
                "not (scrub_failure_result.skipped | default(false) | bool) and "
                "(scrub_failure_result.rc is not defined or "
                "scrub_failure_result.failed | default(false) | bool or "
                "scrub_failure_result.rc | int != 0)"
            )
        if "ansible.builtin.systemd_service" in task:
            service = task.pop("ansible.builtin.systemd_service")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), service["state"], service["name"]]
            }
            task["changed_when"] = True
        if task.get("ansible.builtin.stat", {}).get("path") == "/usr/bin/proxmox-backup-client":
            task["ansible.builtin.stat"]["path"] = str(root / "bin/proxmox-backup-client")
        if "ansible.builtin.uri" in task:
            task["ansible.builtin.uri"]["url"] = task["ansible.builtin.uri"]["url"].replace(
                "localhost", "127.0.0.1")
            task["retries"] = 1
            task["delay"] = 0
        for key in ["block", "rescue", "always"]:
            if key in task:
                task[key] = fixture_tasks(task[key], root)
    return tasks


class VaultwardenHealth(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/alive":
            self.send_response(self.server.response_status)
            self.end_headers()
            return
        if self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Vaultwarden Web")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        pass


def check_restore_runtime(case, server):
    with tempfile.TemporaryDirectory(prefix="homelab-vaultwarden-restore.") as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        data = root / "data"
        has_original = case != "no-original-health-failure"
        if has_original:
            data.mkdir(mode=0o750)
            (data / "db.sqlite3").write_bytes(b"original database\n")
            (data / "config.json").write_text('{"signups_allowed":false}\n', encoding="utf-8")
        before_inode = data.stat().st_ino if has_original else None
        before = ({
            path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
            for path in data.iterdir() if path.is_file()
        } if has_original else {})
        scripts = {
            "proxmox-backup-client": """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
assert os.environ.get('PBS_REPOSITORY') == 'fixture'
case = os.environ['VAULTWARDEN_TEST_CASE']
if args[:2] == ['snapshot', 'list']:
    print('host/vaultwarden-family/2026-10-14T00:00:00Z')
elif args[:2] == ['snapshot', 'files']:
    print(json.dumps([{'filename': 'data.pxar'}]))
elif args and args[0] == 'restore':
    if case == 'extract-failure':
        raise SystemExit(7)
    target = args[3]
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, 'db.sqlite3'), 'wb') as archive:
        archive.write(b'restored database\\n')
    with open(os.path.join(target, 'config.json'), 'w', encoding='utf-8') as archive:
        archive.write('{"signups_allowed":false}\\n')
else:
    raise SystemExit('unexpected PBS client command')
""",
            "service": """#!/usr/bin/env python3
import os, sys
from pathlib import Path
with Path(os.environ['VAULTWARDEN_TEST_ROOT'], 'services').open('a') as log:
    log.write(sys.argv[1] + '\\n')
""",
            "chown": """#!/usr/bin/env python3
import os, subprocess, sys
if os.environ['VAULTWARDEN_TEST_CASE'] == 'ownership-failure':
    raise SystemExit(7)
raise SystemExit(subprocess.run(['/bin/chown', *sys.argv[1:]]).returncode)
""",
        }
        for name, script in scripts.items():
            path = root / "bin" / name
            path.write_text(script, encoding="utf-8")
            path.chmod(0o700)

        snapshot = ("host/foreign/2026-10-09T00:00:00Z" if case == "foreign-group"
                    else "host/vaultwarden-family/2026-10-09T00:00:00Z")
        server.response_status = 503 if "health-failure" in case else 200
        play = {
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "instance": "vaultwarden-family",
                "app_config": {
                    "app": {"data_path": str(data), "service_name": "vaultwarden-family",
                            "port": server.server_port},
                    "backup": {"enabled": True, "application_consistent": True,
                               "backup_id": "", "datastore": "", "retention_days": 14},
                },
                "homelabinfra_infra": {"backups": {}},
                "vaultwarden_user": str(os.getuid()),
                "restore_overwrite": case != "plan",
                "restore_destination": "existing",
                "restore_snapshot": "" if case == "plan" else snapshot,
                "restore_backup_id": "vaultwarden-family",
                "restore_target_backup_id": "vaultwarden-family",
                "restore_pre_restore_point": "host/vaultwarden-family/2026-10-14T00:00:00Z",
            },
            "tasks": fixture_tasks(read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/restore.yml"), root),
        }
        playbook = root / "restore.yml"
        playbook.write_text(yaml.safe_dump([play]), encoding="utf-8")
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "VAULTWARDEN_TEST_CASE": case,
            "VAULTWARDEN_TEST_ROOT": str(root),
        }
        result = subprocess.run(
            [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-c", "local", str(playbook)],
            env=env, text=True, capture_output=True, check=False,
        )
        succeeds = case in ["plan", "success"]
        assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
        after = ({
            path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
            for path in data.iterdir() if path.is_file()
        } if data.exists() else {})
        if case == "success":
            assert data.exists()
            assert (data / "db.sqlite3").read_bytes() == b"restored database\n"
            assert before != after
            assert not (root / "services").read_text().endswith("stopped\n")
        elif case == "no-original-health-failure":
            assert not data.exists(), "rollback must not leave the failed replacement behind"
        else:
            assert data.exists()
            assert data.stat().st_ino == before_inode, "rollback must move the original directory back"
            assert after == before, "a failed or read-only restore must preserve the original data"
        assert not list(root.glob(".vaultwarden-pre-restore-*")), result.stdout + result.stderr
        assert not list(root.glob(".vaultwarden-restore-*")), result.stdout + result.stderr
        if case in ["plan", "foreign-group"]:
            assert not (root / "services").exists(), "read-only/rejected restore stopped the service"
        else:
            assert (root / "services").exists(), f"{case} did not record a service transition: {result.stdout}{result.stderr}"
            states = (root / "services").read_text(encoding="utf-8").splitlines()
            assert states[0] == "stopped"
            assert states[-1] == "started", "a completed rollback must restart the original service state"
        if case == "foreign-group":
            assert "must belong to" in result.stdout


def main():
    check_contract_and_dispatch()
    check_backup_role()
    check_restore_transaction()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), VaultwardenHealth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for case in ["plan", "foreign-group", "extract-failure", "ownership-failure",
                     "health-failure", "no-original-health-failure", "success"]:
            check_restore_runtime(case, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print("Vaultwarden: native recovery contract, plan, selected group, success and post-move rollback passed")


if __name__ == "__main__":
    main()
