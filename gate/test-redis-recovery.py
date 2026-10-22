#!/usr/bin/env python3
"""Keep Redis's native PBS recovery and dedicated LXC removal contract in sync."""

import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text())


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


def check_defaults_and_deploy():
    defaults = read_yaml("ansible/vars/app-defaults/redis.yml")["redis_defaults"]
    assert defaults["recovery"]["methods"] == ["native"]
    assert defaults["recovery"]["native"]["backup_playbook"] == "backup-app.yml"
    assert defaults["recovery"]["native"]["restore_playbook"] == "restore-app.yml"
    assert defaults["backup"]["enabled"] is True
    assert defaults["backup"]["application_consistent"] is True
    assert defaults["backup"]["retention_days"] >= 2
    assert defaults["proxmox"]["guest_type"] == "lxc"
    assert defaults["routing"] == {"proxy": "none", "identity": "none"}
    assert "remove" not in defaults

    redis_play = read_yaml("ansible/playbooks/apps/redis.yml")
    assert not any(
        task.get("ansible.builtin.include_vars", {}).get("name") == "homelabinfra_infra"
        and "facts.yml" in str(task)
        for task in tasks_recursive(redis_play)
    )
    provision = next(play for play in redis_play if play["name"] == "Redis | Provision")
    provision_tasks = list(tasks_recursive(provision))
    placement = task_named(provision_tasks, "Resolve Redis guest placement and check existing type")
    assert placement["ansible.builtin.include_tasks"] == "../../tasks/database/resolve-backend-placement.yml"
    provision_guest = task_named(provision_tasks, "Provision a new Redis backend guest")
    assert provision_guest["ansible.builtin.include_tasks"] == "../../tasks/database/provision-backend-guest.yml"

    redis_main = read_yaml("ansible/roles/redis/tasks/main.yml")
    install_repo = task_named(redis_main, "Redis | Install the PBS client repository when this lab declares app backups")
    assert install_repo["ansible.builtin.include_tasks"] == "install-pbs-client.yml"
    assert "backup.enabled" in str(install_repo["when"])
    apt_refresh = task_named(redis_main, "Redis | Refresh apt metadata when stale")
    assert apt_refresh["ansible.builtin.apt"]["cache_valid_time"] >= 3600
    assert apt_refresh["changed_when"] is False
    redis_package = task_named(redis_main, "Redis | Install server")
    assert redis_package["ansible.builtin.apt"]["update_cache"] is False
    pbs_package = task_named(redis_main, "Redis | Install the PBS backup client")
    assert pbs_package["ansible.builtin.apt"]["update_cache"] is False

    pbs_repo_tasks = read_yaml("ansible/roles/redis/tasks/install-pbs-client.yml")
    repo_source = task_named(pbs_repo_tasks, "Redis PBS client | Configure the signed client repository")
    assert repo_source["register"] == "_redis_pbs_repository_source"
    repo_refresh = task_named(pbs_repo_tasks, "Redis PBS client | Refresh apt metadata when the repository changes")
    assert "_redis_pbs_repository_source.changed" in str(repo_refresh["when"])
    assert repo_refresh["changed_when"] is False

    acl_file = task_named(redis_main, "Redis | Create the ACL file without replacing existing users")
    assert acl_file["ansible.builtin.copy"]["content"] == ""
    assert acl_file["ansible.builtin.copy"]["dest"] == "{{ _redis_acl_path }}"
    assert "path" not in acl_file["ansible.builtin.copy"]
    assert acl_file["ansible.builtin.copy"]["force"] is False
    assert "not _redis_acl_file.stat.exists" in str(acl_file["when"])
    acl_mode = task_named(redis_main, "Redis | Manage the ACL file ownership and mode")
    assert acl_mode["ansible.builtin.file"]["state"] == "file"
    assert acl_mode["ansible.builtin.file"]["mode"] == "0600"
    acl_reconcile = task_named(redis_main, "Redis | Reconcile the default ACL login with Vaultwarden")
    assert acl_reconcile["ansible.builtin.import_tasks"] == "reconcile-default-user.yml"

    template = (ROOT / "ansible/roles/redis/templates/redis.conf.j2").read_text()
    assert "aclfile {{ app_config.app.acl_path" in template
    assert "requirepass" not in template


def run_default_acl_reconciliation(acl_path, playbook_path, password):
    task_path = ROOT / "ansible/roles/redis/tasks/reconcile-default-user.yml"
    playbook = [
        {
            "name": "Exercise Redis default ACL reconciliation",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "_redis_acl_path": str(acl_path),
                "_redis_password": password,
            },
            "tasks": [
                {"name": "Run the role task", "ansible.builtin.import_tasks": str(task_path)}
            ],
            "handlers": [
                {
                    "name": "Restart Redis",
                    "ansible.builtin.debug": {"msg": "Redis restart requested"},
                }
            ],
        }
    ]
    playbook_path.write_text(yaml.safe_dump(playbook, sort_keys=False))
    ansible_playbook = Path(sys.executable).with_name("ansible-playbook")
    assert ansible_playbook.is_file(), "ansible-playbook must be installed beside the gate Python"
    result = subprocess.run(
        [ansible_playbook, "-i", "localhost,", "-c", "local", str(playbook_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"Redis ACL reconciliation failed:\n{result.stdout}\n{result.stderr}"
    recap = next(line for line in result.stdout.splitlines() if line.startswith("localhost") and "changed=" in line)
    changed = re.search(r"changed=(\d+)", recap)
    assert changed, f"Ansible recap did not report changes:\n{recap}"
    return int(changed.group(1))


def check_default_acl_reconciliation():
    tasks = read_yaml("ansible/roles/redis/tasks/reconcile-default-user.yml")
    read_acl = task_named(tasks, "Redis | Read the current default ACL rule")
    collect_rules = task_named(tasks, "Redis | Collect default ACL rules")
    assert read_acl["ansible.builtin.slurp"]["src"] == "{{ _redis_acl_path }}"
    assert read_acl["no_log"] is True
    assert collect_rules.get("no_log") is True
    assert "select('match', '^user default(?: |$)')" in str(collect_rules)

    validate_rules = task_named(tasks, "Redis | Require at most one default ACL rule")
    assert "_redis_default_acl_rules | length <= 1" in str(validate_rules)
    assert validate_rules.get("no_log") is True

    build_rule = task_named(tasks, "Redis | Build the default ACL rule with managed credentials and grants")
    assert "reject('match'" in str(build_rule)
    assert "hash('sha256')" in str(build_rule)
    assert build_rule.get("no_log") is True

    task = task_named(tasks, "Redis | Reconcile the default ACL login with Vaultwarden")
    lineinfile = task["ansible.builtin.lineinfile"]
    assert lineinfile["regexp"] == r"^user default(?: |$)"
    assert lineinfile["line"] == "{{ _redis_default_acl_line }}"
    assert "when" not in task
    assert task["notify"] == "Restart Redis"
    assert task.get("no_log") is True

    with tempfile.TemporaryDirectory(prefix="redis-acl-reconcile-") as directory:
        acl_path = Path(directory) / "users.acl"
        playbook_path = Path(directory) / "play.yml"
        canary_user = "user canary on #" + ("a" * 64) + " ~* &* +@all"
        acl_path.write_text(f"user default on nopass ~* &* +@all\n{canary_user}\n")

        first_password = "redis-test-password-before-rotation"
        first_changes = run_default_acl_reconciliation(acl_path, playbook_path, first_password)
        first_hash = hashlib.sha256(first_password.encode()).hexdigest()
        acl_lines = acl_path.read_text().splitlines()
        assert f"user default on #{first_hash} ~* &* +@all" in acl_lines
        assert canary_user in acl_lines
        assert "nopass" not in "\n".join(acl_lines)
        assert first_password not in "\n".join(acl_lines)
        assert first_changes == 1

        unchanged = run_default_acl_reconciliation(acl_path, playbook_path, first_password)
        assert unchanged == 0

        # Redis ACL SAVE includes user flags in its canonical output. Reconciliation
        # must leave the serialized rule alone so the next Deploy remains unchanged.
        saved_rule = f"user default on sanitize-payload #{first_hash} ~* &* +@all"
        acl_path.write_text(f"{saved_rule}\n{canary_user}\n")
        after_acl_save = run_default_acl_reconciliation(acl_path, playbook_path, first_password)
        acl_lines = acl_path.read_text().splitlines()
        assert after_acl_save == 0
        assert saved_rule in acl_lines
        assert canary_user in acl_lines

        rotated_password = "redis-test-password-after-rotation"
        rotated_changes = run_default_acl_reconciliation(acl_path, playbook_path, rotated_password)
        rotated_hash = hashlib.sha256(rotated_password.encode()).hexdigest()
        acl_lines = acl_path.read_text().splitlines()
        assert f"user default on sanitize-payload #{rotated_hash} ~* &* +@all" in acl_lines
        assert canary_user in acl_lines
        assert rotated_password not in "\n".join(acl_lines)
        assert rotated_changes == 1

        rotation_repeat = run_default_acl_reconciliation(acl_path, playbook_path, rotated_password)
        assert rotation_repeat == 0

        empty_acl_path = Path(directory) / "empty-users.acl"
        empty_acl_path.write_text(f"{canary_user}\n")
        empty_acl_changes = run_default_acl_reconciliation(empty_acl_path, playbook_path, first_password)
        empty_acl_lines = empty_acl_path.read_text().splitlines()
        assert f"user default on #{first_hash} ~* &* +@all" in empty_acl_lines
        assert canary_user in empty_acl_lines
        assert empty_acl_changes == 1


def check_backup():
    backup = read_yaml("ansible/roles/redis/tasks/backup.yml")
    export = read_yaml("ansible/roles/redis/tasks/backup-export.yml")
    backup_tasks = list(tasks_recursive(backup)) + list(tasks_recursive(export))

    credentials = task_named(
        list(tasks_recursive(backup)), "Redis backup | Resolve paths and the recovery identity"
    )
    assert "homelabinfra_vault.apps" in str(
        credentials["ansible.builtin.set_fact"]["_redis_backup_password"]
    )
    require_password = task_named(
        list(tasks_recursive(backup)), "Redis backup | Require the backend password in Vaultwarden"
    )
    assert require_password.get("no_log") is True
    assert "_redis_backup_password" in str(require_password["ansible.builtin.assert"]["that"])

    export_block = task_named(list(tasks_recursive(backup)), "Redis backup | Export and upload the recovery point")
    assert "backup-export.yml" in str(export_block["block"])
    assert "always" in export_block
    assert "_redis_backup_stage.path" in str(export_block["always"])

    pause = task_named(backup_tasks, "Redis backup | Pause write commands while the point is captured")
    pause_argv = pause["ansible.builtin.command"]["argv"]
    pause_index = pause_argv.index("CLIENT")
    assert pause_argv[pause_index:pause_index + 2] == ["CLIENT", "PAUSE"]
    assert pause["ansible.builtin.command"]["argv"][-1] == "WRITE"
    assert pause.get("no_log") is True
    unpause = task_named(backup_tasks, "Redis backup | Resume Redis write commands")
    unpause_argv = unpause["ansible.builtin.command"]["argv"]
    unpause_index = unpause_argv.index("CLIENT")
    assert unpause_argv[unpause_index:unpause_index + 2] == ["CLIENT", "UNPAUSE"]
    assert unpause.get("no_log") is True

    acl_save = task_named(backup_tasks, "Redis backup | Persist the current Redis ACL users")
    acl_mode = task_named(backup_tasks, "Redis backup | Restore the ACL file ownership and mode")
    rdb = task_named(backup_tasks, "Redis backup | Export a consistent RDB through Redis replication")
    acl_copy = task_named(backup_tasks, "Redis backup | Copy the persisted ACL users into the recovery point")
    for task in (pause, acl_save, rdb, unpause):
        assert task["environment"]["REDISCLI_AUTH"] == "{{ _redis_backup_password }}"
    assert acl_save.get("no_log") is True
    assert acl_mode["ansible.builtin.file"]["owner"] == "redis"
    assert acl_mode["ansible.builtin.file"]["group"] == "redis"
    assert acl_mode["ansible.builtin.file"]["mode"] == "0600"
    assert "--rdb" in rdb["ansible.builtin.command"]["argv"]
    assert rdb.get("no_log") is True
    assert acl_copy["ansible.builtin.copy"]["remote_src"] is True
    assert backup_tasks.index(pause) < backup_tasks.index(acl_save) < backup_tasks.index(acl_mode) < backup_tasks.index(rdb)
    assert backup_tasks.index(rdb) < backup_tasks.index(acl_copy)

    upload = task_named(backup_tasks, "Redis backup | Upload the native export to PBS")
    assert "data.pxar:{{ _redis_backup_stage.path }}" in upload["ansible.builtin.command"]["argv"]
    assert set(upload["environment"]) == {"PBS_REPOSITORY", "PBS_FINGERPRINT", "PBS_PASSWORD"}
    assert upload.get("no_log") is True
    snapshot_delta = task_named(backup_tasks, "Redis backup | Require one new recovery point")
    assert "_redis_backup_added_snapshot_times | length == 1" in str(snapshot_delta["ansible.builtin.assert"]["that"])
    prune = task_named(backup_tasks, "Redis backup | Retain recent and daily recovery points")
    assert prune["ansible.builtin.command"]["argv"][prune["ansible.builtin.command"]["argv"].index("--keep-last") + 1] == "2"


def check_restore():
    restore = read_yaml("ansible/roles/redis/tasks/restore.yml")
    tasks = list(tasks_recursive(restore))

    source_guard = task_named(tasks, "Redis restore | Require the selected point to belong to its source group")
    assert "_redis_restore_source_backup_id" in str(source_guard["ansible.builtin.assert"]["that"])
    prepoint_guard = task_named(tasks, "Redis restore | Require an independent existing-target point")
    assert "_redis_restore_target_backup_id" in str(prepoint_guard["ansible.builtin.assert"]["that"])

    extract = task_named(tasks, "Redis restore | Extract the selected archive to staging")
    staged_paths = task_named(tasks, "Redis restore | Validate staged native files")
    validate = task_named(tasks, "Redis restore | Require a complete staged export")
    transaction = task_named(tasks, "Redis restore | Replace the data directory transactionally")
    stop = task_named(tasks, "Redis restore | Stop Redis before replacing data")
    original = task_named(tasks, "Redis restore | Move the original data aside on the same filesystem")
    rollback = task_named(tasks, "Redis restore | Put the original data back")
    assert "dump.rdb" in str(staged_paths["loop"])
    assert "users.acl" in str(staged_paths["loop"])
    assert "not item.stat.islnk" in str(validate["ansible.builtin.assert"]["that"])
    assert extract.get("no_log") is True
    assert "--allow-existing-dirs" in extract["ansible.builtin.command"]["argv"]
    assert tasks.index(extract) < tasks.index(validate) < tasks.index(transaction)
    transaction_block = transaction["block"]
    assert transaction_block.index(stop) < transaction_block.index(original)
    assert "mv" in rollback["ansible.builtin.command"]["argv"]

    align = task_named(tasks, "Redis restore | Align the default user with the target Vaultwarden password")
    align_argv = align["ansible.builtin.command"]["argv"]
    assert align_argv[align_argv.index("ACL"):] == ["ACL", "SETUSER", "default", "resetpass"]
    assert "-x" in align_argv
    assert "_redis_restore_target_password" in align["ansible.builtin.command"]["stdin"]
    assert align["ansible.builtin.command"]["stdin_add_newline"] is False
    assert align.get("no_log") is True
    reconcile = task_named(tasks, "Redis restore | Persist the reconciled ACL file")
    assert reconcile.get("no_log") is True
    restore_credentials = task_named(
        tasks, "Redis restore | Resolve paths, credentials and recovery identities"
    )["ansible.builtin.set_fact"]["_redis_restore_target_password"]
    assert "homelabinfra_vault.apps" in str(restore_credentials)
    assert "restore_target | default(instance, true)" in str(restore_credentials)
    acl_mode = task_named(tasks, "Redis restore | Restore the ACL file ownership and mode")
    assert acl_mode["ansible.builtin.file"]["owner"] == "redis"
    assert acl_mode["ansible.builtin.file"]["group"] == "redis"
    assert acl_mode["ansible.builtin.file"]["mode"] == "0600"
    assert tasks.index(reconcile) < tasks.index(acl_mode)

    target_auth = task_named(tasks, "Redis restore | Verify the target password after loading the ACL file")
    source_auth = task_named(tasks, "Redis restore | Verify the source password when the target password differs")
    for probe, registered in (
        (target_auth, "_redis_restore_target_auth"),
        (source_auth, "_redis_restore_source_auth"),
    ):
        assert probe["until"] == f"{registered}.rc == 0"
        assert probe["retries"] == 30
        assert probe["delay"] == 2
        assert probe["failed_when"] is False
        assert probe.get("no_log") is True

    plan = task_named(tasks, "Redis restore | Show the read-only recovery point plan")
    assert "not (restore_overwrite | bool)" in plan["when"]


def check_remove_scope():
    remove = read_yaml("ansible/playbooks/apps/remove.yml")
    unwire_play = remove[0]
    pre_tasks = list(tasks_recursive(unwire_play["pre_tasks"]))
    explicit_request = task_named(pre_tasks, "Validate the explicit guest destruction request before unwiring")
    ownership = task_named(pre_tasks, "Verify ownership and non-shared tags before unwiring")
    assert "delete_data" in str(explicit_request["ansible.builtin.assert"]["that"])
    assert "_rm_kind == 'native'" in str(explicit_request["ansible.builtin.assert"]["that"])
    assert "_rm_destroy_guest_host == instance" in str(explicit_request["ansible.builtin.assert"]["that"])
    assert "_+lab" in str(ownership["ansible.builtin.assert"]["that"])
    assert "in ['lxc', 'qemu']" in str(ownership["ansible.builtin.assert"]["that"])
    assert "proxmox_vmtype" in str(ownership["ansible.builtin.assert"]["that"])
    assert "_.shared" in str(ownership["ansible.builtin.assert"]["that"])
    assert task_named(unwire_play["tasks"], "Unwire reverse proxy") in unwire_play["tasks"]

    destroy = task_named(list(tasks_recursive(remove)), "Destroy the explicitly selected native LXC")
    assert destroy["ansible.builtin.include_tasks"] == "../../tasks/proxmox/destroy-owned-lxc.yml"
    assert "_rm_destroy_guest | bool" in str(destroy["when"])
    assert "_rm_delete_data | bool" in str(destroy["when"])
    assert "remove_kind == 'native'" in str(destroy["when"])

    destroy_vm = task_named(list(tasks_recursive(remove)), "Destroy the explicitly selected native VM")
    assert destroy_vm["ansible.builtin.include_tasks"] == "../../tasks/proxmox/destroy-owned-vm.yml"
    assert "proxmox_vmtype == 'qemu'" in str(destroy_vm["when"])
    vm_destroy_tasks = read_yaml("ansible/tasks/proxmox/destroy-owned-vm.yml")
    vm_live_guard = task_named(vm_destroy_tasks, "Destroy owned VM | Require current lab ownership and an unshared guest")
    assert "_+lab" in str(vm_live_guard["ansible.builtin.assert"]["that"])
    assert "_.shared" in str(vm_live_guard["ansible.builtin.assert"]["that"])
    assert "destroy_vm_instance" in str(vm_live_guard["ansible.builtin.assert"]["that"])
    vm_destroy = task_named(vm_destroy_tasks, "Destroy owned VM | Destroy the stopped guest")
    vm_absent = task_named(vm_destroy_tasks, "Destroy owned VM | Require the VMID to be absent")
    assert vm_destroy["ansible.builtin.command"]["argv"] == [
        "qm", "destroy", "{{ destroy_vm_vmid }}", "--purge", "1"
    ]
    assert vm_destroy_tasks.index(vm_destroy) < vm_destroy_tasks.index(vm_absent)

    destroy_tasks = read_yaml("ansible/tasks/proxmox/destroy-owned-lxc.yml")
    live_guard = task_named(destroy_tasks, "Destroy owned LXC | Require current lab ownership and an unshared guest")
    assert "_+lab" in str(live_guard["ansible.builtin.assert"]["that"])
    assert "_.shared" in str(live_guard["ansible.builtin.assert"]["that"])
    assert "destroy_lxc_instance" in str(live_guard["ansible.builtin.assert"]["that"])
    shutdown = task_named(destroy_tasks, "Destroy owned LXC | Shut down the guest cleanly")
    confirm_stopped = task_named(destroy_tasks, "Destroy owned LXC | Confirm shutdown completed")
    destroy_guest = task_named(destroy_tasks, "Destroy owned LXC | Destroy the stopped guest")
    verify_absent = task_named(destroy_tasks, "Destroy owned LXC | Require the VMID to be absent")
    assert destroy_guest["ansible.builtin.command"]["argv"] == ["pct", "destroy", "{{ destroy_lxc_vmid }}"]
    assert destroy_tasks.index(shutdown) < destroy_tasks.index(confirm_stopped) < destroy_tasks.index(destroy_guest) < destroy_tasks.index(verify_absent)


def main():
    check_defaults_and_deploy()
    check_default_acl_reconciliation()
    check_backup()
    check_restore()
    check_remove_scope()
    print("Redis: native LXC deploy, ACL and RDB PBS recovery, transactional restore, and scoped removal passed")


if __name__ == "__main__":
    main()
