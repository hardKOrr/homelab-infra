#!/usr/bin/env python3
"""Guard Vaultwarden's native recovery and additional-instance isolation contract."""

from pathlib import Path
import copy
import http.server
import importlib.util
import json
import os
import subprocess
import tempfile
import threading

import yaml

from pbs_client_stub import stub_pbs_client


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
    retained = task_named(deploy_tasks, "Resolve this instance's guest, reusing one Remove kept")
    assert retained["ansible.builtin.include_tasks"] == "../../tasks/proxmox/resolve-instance-guest.yml"
    assert retained["vars"]["resolve_guest_type"] == "lxc"
    provision = task_named(deploy_tasks, "Provision new LXC")
    assert provision["when"] == "_existing_hosts | length == 0"
    create_guard = read_yaml(ROOT / "ansible/tasks/proxmox/lxc-create.yml")
    identity_guard = task_named(create_guard, "Refuse existing identity at the LXC creation seam")
    identity_checks = str(identity_guard["ansible.builtin.assert"]["that"])
    assert "proxmox_vmid" in identity_checks and "proxmox_hostname" in identity_checks
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
        "databases": {
            "mariadb-bookstack": {"provider": "mariadb", "host": "db.invalid", "port": 3306},
            "mariadb-stage": {"provider": "mariadb", "host": "stage.invalid", "port": 3306},
        },
    }
    assert registry_module.prune(registry, "vaultwarden-family") == {
        "vaultwarden": {"host": "http://root-vault.invalid", "port": 80},
        "databases": registry["databases"],
    }
    consumer_registry = {
        "apps": {"bookstack": {"host": "http://bookstack.invalid", "port": 80}},
        "databases": {
            "mariadb-bookstack": {"provider": "mariadb", "host": "db.invalid", "port": 3306},
        },
    }
    assert registry_module.prune(consumer_registry, "bookstack") == {
        "databases": consumer_registry["databases"],
    }
    assert registry_module.prune(registry, "mariadb-stage", database_backend=True) == {
        "vaultwarden": {"host": "http://root-vault.invalid", "port": 80},
        "apps": {"vaultwarden-family": {"host": "http://family.invalid", "port": 80}},
        "databases": {
            "mariadb-bookstack": {"provider": "mariadb", "host": "db.invalid", "port": 3306},
        },
    }

    remove = read_yaml(ROOT / "ansible/playbooks/apps/remove.yml")
    remove_pre_tasks = list(tasks_recursive(remove[0]))
    absence_candidate = task_named(
        remove_pre_tasks,
        "Identify a registered database backend missing from the inventory",
    )
    missing_backend_expr = str(
        absence_candidate["ansible.builtin.set_fact"]["_rm_database_backend_inventory_absence_candidate"]
    )
    assert "homelabinfra_infra" in missing_backend_expr
    assert "databases" in missing_backend_expr
    assert "_rm_destroy_guest_host | length == 0" in missing_backend_expr
    assert "proxmox_name" in missing_backend_expr and "equalto" in missing_backend_expr
    resources = task_named(
        remove_pre_tasks,
        "Read Proxmox cluster guests before pruning an absent database backend",
    )
    assert resources["ansible.builtin.command"]["argv"] == [
        "pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json",
    ]
    assert resources["delegate_to"] == "{{ homelabinfra_config.proxmox.node }}"
    require_resources = task_named(remove_pre_tasks, "Require Proxmox to return its current cluster guests")
    assert "| length > 0" in str(require_resources["ansible.builtin.assert"]["that"])
    live_match = task_named(remove_pre_tasks, "Check Proxmox resources for the registered backend name or tag")
    assert "item.name" in str(live_match["ansible.builtin.set_fact"])
    assert "item.tags" in str(live_match["ansible.builtin.set_fact"])
    live_absence = task_named(remove_pre_tasks, "Refuse absent-backend cleanup while Proxmox reports the guest")
    assert "_rm_database_backend_live_guest_matches" in str(live_absence["ansible.builtin.assert"])
    validate_destroy = task_named(
        remove_pre_tasks,
        "Validate the explicit guest destruction request before unwiring",
    )
    validate_checks = str(validate_destroy["ansible.builtin.assert"]["that"])
    assert "_rm_database_backend_missing_guest" in validate_checks
    assert "delete_data" in validate_checks and "_rm_kind == 'native'" in validate_checks
    ownership = task_named(remove_pre_tasks, "Verify ownership and non-shared tags before unwiring")
    assert "_rm_destroy_guest_host | length > 0" in str(ownership["when"])

    play_names = [play.get("name", "") for play in remove]
    destroy_index = next(
        i for i, name in enumerate(play_names) if name.startswith("Remove App | Stop")
    )
    registry_index = play_names.index(
        "Remove Database Backend | Unregister destroyed backend"
    )
    notify_index = play_names.index("Remove App | Notify")
    assert destroy_index < registry_index < notify_index
    unregister = next(
        task for task in tasks_recursive(remove[registry_index])
        if task.get("name") == "Forget the destroyed database backend's generated registry entry"
    )
    assert unregister["ansible.builtin.include_tasks"] == (
        "../../tasks/unwiring/registry.yml"
    )
    assert unregister["vars"]["unwire_registry_database_backend"] is True
    assert "destroy_guest" in str(unregister["when"])
    assert "delete_data" in str(unregister["when"])

    main = list(tasks_recursive(read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/main.yml")))
    install = task_named(main, "Install the pinned PBS client when this lab declares application backups")
    assert install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-install.yml")


def check_remove_absent_database_backend_cleanup():
    remove = read_yaml(ROOT / "ansible/playbooks/apps/remove.yml")
    remove_pre_tasks = list(tasks_recursive(remove[0]))
    resolve = task_named(remove_pre_tasks, "Resolve the exact guest selected for explicit destruction")
    absence_candidate = task_named(
        remove_pre_tasks,
        "Identify a registered database backend missing from the inventory",
    )
    register_nodes = task_named(remove_pre_tasks, "Register Proxmox nodes before checking an absent database backend")
    read_resources = task_named(
        remove_pre_tasks,
        "Read Proxmox cluster guests before pruning an absent database backend",
    )
    require_resources = task_named(remove_pre_tasks, "Require Proxmox to return its current cluster guests")
    live_match = task_named(remove_pre_tasks, "Check Proxmox resources for the registered backend name or tag")
    live_absence = task_named(remove_pre_tasks, "Refuse absent-backend cleanup while Proxmox reports the guest")
    confirm_absent = task_named(remove_pre_tasks, "Confirm the registered database backend is absent from Proxmox")
    validate = task_named(
        remove_pre_tasks,
        "Validate the explicit guest destruction request before unwiring",
    )
    ownership = task_named(remove_pre_tasks, "Verify ownership and non-shared tags before unwiring")

    def run_case(
        instance,
        app,
        database_instances,
        guest=None,
        expected_missing=True,
        failure=None,
        cluster_guests=None,
    ):
        with tempfile.TemporaryDirectory(prefix="homelab-remove-database.") as directory:
            root = Path(directory)
            hosts = {
                "localhost": {"ansible_connection": "local"},
                "pve-control": {"ansible_connection": "local"},
            }
            if guest is not None:
                hosts[instance] = guest
            inventory = {"all": {"hosts": hosts}}
            inventory_path = root / "inventory.yml"
            inventory_path.write_text(yaml.safe_dump(inventory), encoding="utf-8")

            bin_dir = root / "bin"
            bin_dir.mkdir()
            pvesh_stub = bin_dir / "pvesh"
            pvesh_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "if os.environ.get('PVE_RESOURCES_FAIL') == '1':\n"
                "    sys.exit(1)\n"
                "sys.stdout.write(pathlib.Path(os.environ['PVE_RESOURCES_FIXTURE']).read_text())\n",
                encoding="utf-8",
            )
            pvesh_stub.chmod(0o755)
            resources_path = root / "cluster-resources.json"
            if cluster_guests is None:
                cluster_guests = [{"name": "other-guest", "tags": "_+lab;_other-guest"}]
            resources_path.write_text(json.dumps(cluster_guests), encoding="utf-8")

            playbook = root / "remove-guard.yml"
            databases = {name: {"provider": app} for name in database_instances}
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {
                    "instance": instance,
                    "app": app,
                    "delete_data": True,
                    "destroy_guest": True,
                    "_rm_kind": "native",
                    "_rm_destroy_guest_host": "",
                    "homelabinfra_infra": {"databases": databases},
                    "homelabinfra_config": {
                        "ansible": {"ssh_user": "root"},
                        "proxmox": {
                            "node": "pve-control",
                            "nodes": {"pve-control": "127.0.0.1"},
                        },
                    },
                    "expected_missing_backend": expected_missing,
                },
                "tasks": [
                    copy.deepcopy(resolve),
                    copy.deepcopy(absence_candidate),
                    {
                        **copy.deepcopy(register_nodes),
                        "ansible.builtin.include_tasks": str(
                            ROOT / "ansible/tasks/proxmox/register-nodes.yml"
                        ),
                    },
                    copy.deepcopy(read_resources),
                    copy.deepcopy(require_resources),
                    copy.deepcopy(live_match),
                    copy.deepcopy(live_absence),
                    copy.deepcopy(confirm_absent),
                    copy.deepcopy(validate),
                    copy.deepcopy(ownership),
                    {"name": "Check absent-backend classification", "ansible.builtin.assert": {
                        "that": [
                            "(_rm_database_backend_missing_guest | default(false) | bool) == expected_missing_backend",
                        ],
                    }},
                ],
            }]), encoding="utf-8")
            result = subprocess.run(
                [str(ANSIBLE_PLAYBOOK), "-i", str(inventory_path), str(playbook)],
                env=os.environ | {
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "PVE_RESOURCES_FIXTURE": str(resources_path),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            output = result.stdout + result.stderr
            if failure is not None:
                assert result.returncode != 0 and failure in output, output
            else:
                assert result.returncode == 0, output

    owned_guest = {
        "proxmox_name": "postgresql",
        "proxmox_vmtype": "lxc",
        "proxmox_vmid": 402,
        "proxmox_node": "pve-a",
        "proxmox_tags_parsed": ["_+lab", "_postgresql"],
    }
    unowned_guest = {
        **owned_guest,
        "proxmox_tags_parsed": ["_+lab", "_other-app"],
    }
    run_case("postgresql", "postgresql", ["postgresql"])
    run_case("mariadb-mautic-stage", "mariadb", ["mariadb-mautic-stage"])
    run_case("missing-app", "immich", [], failure="destroy_guest=true requires delete_data=true")
    run_case(
        "postgresql",
        "postgresql",
        ["postgresql"],
        failure="Refusing to unregister the database backend",
        cluster_guests=[{"name": "postgresql", "tags": "_+lab;_postgresql"}],
    )
    run_case(
        "postgresql",
        "postgresql",
        ["postgresql"],
        failure="Refusing to unregister the database backend",
        cluster_guests=[],
    )
    run_case(
        "postgresql",
        "postgresql",
        ["postgresql"],
        failure="Refusing to unregister the database backend",
        cluster_guests=[{"name": "renamed-guest", "tags": "_+lab;_postgresql"}],
    )
    run_case("postgresql", "postgresql", ["postgresql"], guest=owned_guest, expected_missing=False)
    run_case(
        "postgresql",
        "postgresql",
        ["postgresql"],
        guest=unowned_guest,
        expected_missing=False,
        failure="Refusing destroy_guest=true",
    )


def check_backup_role():
    tasks = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/backup.yml")
    upload = task_named(tasks_recursive(tasks), "Vaultwarden backup | Quiesce and archive the application")
    assert upload["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-backup.yml")
    assert upload["vars"]["pbs_client_runtime"] == "native"
    assert upload["vars"]["pbs_client_stop_services"] == ["{{ _vw_backup_service_name }}"]
    assert upload["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _vw_backup_data_path }}"}
    ]
    assert any(task.get("name") == "Vaultwarden backup | Check the SQLite database"
               for task in tasks_recursive(tasks))
    backup_play = next(play for play in read_yaml(ROOT / "ansible/playbooks/maintenance/backup-app.yml")
                       if play.get("name") == "Backup App | Run the application-owned backup")
    assert backup_play["tasks"][-1]["name"] == "Fail if application backup degraded"


def check_restore_transaction():
    tasks = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/restore.yml")
    all_tasks = list(tasks_recursive(tasks))
    assert "Vaultwarden restore | Validate the selected source group" in [task.get("name") for task in all_tasks]
    assert "Vaultwarden restore | Validate the independent pre-restore group" in [task.get("name") for task in all_tasks]
    preview = task_named(all_tasks, "Vaultwarden restore | List or verify the recovery point before changing the vault")
    assert preview["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert preview["vars"]["pbs_client_archives"] == [{"name": "data.pxar"}]
    assert preview["vars"]["pbs_client_overwrite"] is False and "when" not in preview

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
    assert extract["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-restore.yml")
    assert extract["vars"]["pbs_client_archives"] == [
        {"name": "data.pxar", "path": "{{ _vw_restore_stage.path }}/data"}
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
        stub_pbs_client(task, root / "bin/proxmox-backup-client")
        if "ansible.builtin.systemd_service" in task:
            service = task.pop("ansible.builtin.systemd_service")
            task["ansible.builtin.command"] = {
                "argv": [str(root / "bin/service"), service["state"], service["name"]]
            }
            task["changed_when"] = True
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
    check_remove_absent_database_backend_cleanup()
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
    print("Vaultwarden recovery and Remove: contract, isolation, absent-backend cleanup, and rollback passed")


if __name__ == "__main__":
    main()
