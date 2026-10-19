#!/usr/bin/env python3
"""Keep Redis's native PBS recovery and dedicated LXC removal contract in sync."""

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
    assert defaults["proxmox"]["type"] == "vm"
    assert defaults["routing"] == {"proxy": "none", "identity": "none"}
    assert defaults["remove"]["destroy_guest_after_data_deletion"] is False

    redis_play = read_yaml("ansible/playbooks/apps/redis.yml")
    assert not any(
        task.get("ansible.builtin.include_vars", {}).get("name") == "homelabinfra_infra"
        and "facts.yml" in str(task)
        for task in tasks_recursive(redis_play)
    )
    provision = next(play for play in redis_play if play["name"] == "Redis | Provision")
    provision_tasks = list(tasks_recursive(provision))
    lxc = task_named(provision_tasks, "Provision Redis LXC")
    assert "_existing_hosts | length == 0" in str(lxc["when"])
    assert "app_config.proxmox.type" in str(lxc["when"])
    assert "../../tasks/proxmox/lxc-create.yml" in str(lxc)
    vm = task_named(provision_tasks, "Provision Redis VM")
    assert "app_config.proxmox.type" in str(vm["when"])

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
    assert acl_file["ansible.builtin.copy"]["force"] is False
    assert "not _redis_acl_file.stat.exists" in str(acl_file["when"])
    acl_mode = task_named(redis_main, "Redis | Manage the ACL file ownership and mode")
    assert acl_mode["ansible.builtin.file"]["state"] == "file"
    assert acl_mode["ansible.builtin.file"]["mode"] == "0600"
    seed_acl = task_named(redis_main, "Redis | Seed the default ACL login from Vaultwarden when it is missing")
    assert "hash('sha256')" in seed_acl["ansible.builtin.lineinfile"]["line"]
    assert "_redis_acl_has_default_user" in str(seed_acl["when"])
    assert seed_acl.get("no_log") is True

    template = (ROOT / "ansible/roles/redis/templates/redis.conf.j2").read_text()
    assert "aclfile {{ app_config.app.acl_path" in template
    assert "requirepass" not in template


def check_backup():
    backup = read_yaml("ansible/roles/redis/tasks/backup.yml")
    export = read_yaml("ansible/roles/redis/tasks/backup-export.yml")
    backup_tasks = list(tasks_recursive(backup)) + list(tasks_recursive(export))

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
    rdb = task_named(backup_tasks, "Redis backup | Export a consistent RDB through Redis replication")
    acl_copy = task_named(backup_tasks, "Redis backup | Copy the persisted ACL users into the recovery point")
    assert acl_save.get("no_log") is True
    assert "--rdb" in rdb["ansible.builtin.command"]["argv"]
    assert rdb.get("no_log") is True
    assert acl_copy["ansible.builtin.copy"]["remote_src"] is True
    assert backup_tasks.index(pause) < backup_tasks.index(acl_save) < backup_tasks.index(rdb)
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

    plan = task_named(tasks, "Redis restore | Show the read-only recovery point plan")
    assert "not (restore_overwrite | bool)" in plan["when"]


def check_remove_scope():
    remove = read_yaml("ansible/playbooks/apps/remove.yml")
    unwire_play = remove[0]
    pre_tasks = list(tasks_recursive(unwire_play["pre_tasks"]))
    ownership = task_named(pre_tasks, "Verify the exact lab-owned LXC before removal")
    preflight = task_named(pre_tasks, "Find the opted-in native LXC before unwiring")
    assert "delete_data" in str(preflight["when"])
    assert "_+lab" in str(ownership["ansible.builtin.assert"]["that"])
    assert "proxmox_vmtype" in str(ownership["ansible.builtin.assert"]["that"])
    assert preflight in unwire_play["pre_tasks"]
    assert task_named(unwire_play["tasks"], "Unwire reverse proxy") in unwire_play["tasks"]

    destroy_play = next(play for play in remove if play["name"] == "Remove App | Destroy the dedicated native LXC")
    destroy = task_named(list(tasks_recursive(destroy_play)), "Destroy the exact owned native LXC after deleting its data")
    module = destroy["community.proxmox.proxmox"]
    assert module["state"] == "absent"
    assert module["purge"] is True
    assert module["destroy_unreferenced_disks"] is True
    assert "_rm_destroy_guest_after_data_deletion" in str(destroy["when"])


def main():
    check_defaults_and_deploy()
    check_backup()
    check_restore()
    check_remove_scope()
    print("Redis: native LXC deploy, ACL and RDB PBS recovery, transactional restore, and scoped removal passed")


if __name__ == "__main__":
    main()
