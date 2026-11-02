#!/usr/bin/env python3
"""Guard Vaultwarden's native recovery and additional-instance isolation contract.

Checks: Vaultwarden's native recovery contract, additional-instance isolation, Remove with
an absent database backend, and the restore swap inputs with the `db.sqlite3` staged hook
and the web vault check.
"""

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

from restore_swap_inputs import swap_inputs


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
            # The facts the app's remove-inputs.yml hook declares, such as
            # _rm_database_backend, set as Remove's hook would set them.
            hook = ROOT / f"ansible/roles/{app}/tasks/remove-inputs.yml"
            declared = {
                name: value
                for task in (read_yaml(hook) if hook.is_file() else [])
                for name, value in (task.get("ansible.builtin.set_fact") or {}).items()
                if name.startswith("_rm_") and isinstance(value, bool)
            }
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
                    *([{"name": "Declare the app's removal facts",
                        "ansible.builtin.set_fact": declared}] if declared else []),
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


class VaultwardenWeb(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/"
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.server.page)

    def log_message(self, *_args):
        pass


def run_hook(hook, stage, port):
    tasks = read_yaml(ROOT / f"ansible/roles/vaultwarden/tasks/restore-{hook}.yml")
    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "hook.yml"
        playbook.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"recovery_swap_stage": str(stage), "app_config": {"app": {"port": port}}},
            "tasks": tasks,
        }]), encoding="utf-8")
        env = os.environ | {"ANSIBLE_NOCOLOR": "1"}
        return subprocess.run([str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-c", "local", str(playbook)],
                              env=env, text=True, capture_output=True, check=False)


def check_restore_swap():
    """Vaultwarden supplies its path, owner and checks; gate/test-restore-swap.py proves the swap."""
    swap = swap_inputs("vaultwarden")
    [path] = swap["recovery_swap_paths"]
    assert path == {"path": "{{ app_config.app.data_path }}", "owner": "{{ vaultwarden_user }}",
                    "group": "{{ vaultwarden_user }}", "mode": "0750"}
    assert swap["recovery_swap_runtime"] == "native"
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _vw_restore_target_backup_id }}"
    assert swap["recovery_swap_stop_services"] == [
        "{{ app_config.app.service_name | default(vaultwarden_service_name) }}"]
    assert swap["recovery_swap_health_url"].endswith("/alive")
    tasks = read_yaml(ROOT / "ansible/roles/vaultwarden/tasks/restore.yml")
    assert "Vaultwarden restore | Validate the independent pre-restore group" in [t.get("name") for t in tasks]

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), VaultwardenWeb)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "data").mkdir()
            assert run_hook("staged", stage, server.server_port).returncode != 0, "no db.sqlite3"
            (stage / "data/db.sqlite3").mkdir()
            assert run_hook("staged", stage, server.server_port).returncode != 0, "db.sqlite3 not a file"
            (stage / "data/db.sqlite3").rmdir()
            (stage / "data/db.sqlite3").write_bytes(b"restored database\n")
            result = run_hook("staged", stage, server.server_port)
            assert result.returncode == 0, result.stdout + result.stderr
            for page, ok in [(b"Not the vault", False), (b"<title>Vaultwarden Web</title>", True)]:
                server.page = page
                result = run_hook("verified", stage, server.server_port)
                assert (result.returncode == 0) == ok, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    check_contract_and_dispatch()
    check_remove_absent_database_backend_cleanup()
    check_backup_role()
    check_restore_swap()
    print("Vaultwarden recovery and Remove: contract, isolation, absent-backend cleanup, swap inputs and restore hooks passed")


if __name__ == "__main__":
    main()
