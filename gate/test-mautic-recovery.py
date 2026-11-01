#!/usr/bin/env python3
"""Check Mautic's paired MariaDB/PBS archive, its restore-swap inputs and hooks.

gate/test-restore-swap.py proves the swap, the database dump, load and rollback; the
replaced hook here points the restored local.php at the target's database and URL.

Checks: Mautic's one-archive backup, its restore swap inputs, the staged hook refusing a
point without `local.php` or media, and the replaced hook pointing the restored `local.php`
at the target in a real play, with the same PHP escaping as Deploy.
"""

import tempfile
from pathlib import Path

import yaml

from restore_swap_inputs import run_hook, swap_inputs

repo = Path(__file__).resolve().parents[1]
defaults = yaml.safe_load((repo / "ansible/vars/app-defaults/mautic.yml").read_text())
catalog = yaml.safe_load((repo / "catalog/applications.yml").read_text())
backup_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/backup.yml").read_text())
replaced_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/restore-replaced.yml").read_text())
deploy_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/docker.yml").read_text())
restore_source = yaml.safe_load((repo / "ansible/roles/mautic/tasks/restore-docker.yml").read_text())
compose_template = (repo / "ansible/roles/mautic/templates/docker-compose.yml.j2").read_text()


def tasks_recursive(tasks):
    for task in tasks:
        yield task
        for key in ["block", "rescue", "always"]:
            yield from tasks_recursive(task.get(key, []))


def check_restore_swap():
    swap = swap_inputs("mautic", task_file="restore-docker.yml")
    assert swap["recovery_swap_archives"] == ["data.pxar"]
    assert swap["recovery_swap_paths"] == [{
        "path": "{{ _mautic_restore_data_path }}", "source": "data/mautic",
        "owner": "www-data", "group": "www-data", "mode": "0750"}]
    assert swap["recovery_swap_database"] == {
        "config": "{{ app_config.app.database }}", "dump": "data/database/mautic.sql"}
    assert swap["recovery_swap_pre_restore_backup_id"] == "{{ _mautic_restore_target_backup_id }}"
    assert swap["recovery_swap_stop_services"] == ["mautic_web", "mautic_cron", "mautic_worker"]
    assert swap["recovery_swap_compose_project"] == "{{ mautic_project_dir }}"
    assert swap["recovery_swap_health_status"] == [200, 302]

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stage = root / "stage/data/mautic"
        variables = {"recovery_swap_stage": str(root / "stage")}
        (stage / "media/files").mkdir(parents=True)
        (stage / "config").mkdir()
        assert run_hook("mautic", "staged", variables).returncode != 0, "no local.php"
        (stage / "config/local.php").write_text("<?php\n")
        assert run_hook("mautic", "staged", variables).returncode != 0, "no media/images"
        (stage / "media/images").mkdir()
        result = run_hook("mautic", "staged", variables)
        assert result.returncode == 0, result.stdout + result.stderr

        data = root / "data"
        (data / "config").mkdir(parents=True)
        (data / "config/local.php").write_text(
            "<?php\n$parameters = array(\n  'db_host' => 'source-db',\n"
            "  'default_timezone' => 'Pacific/Honolulu',\n);\n")

        template = repo / "ansible/roles/mautic/templates/php-string.j2"

        def stub(tasks):
            # The play runs as root on the Docker host; the fixture writes as the gate user
            # and reads no lab config.
            for task in tasks:
                task.pop("no_log", None)
                if "ansible.builtin.include_vars" in task:
                    task["ansible.builtin.include_vars"]["file"] = str(root / "no-lab-config.yml")
                line = task.get("ansible.builtin.lineinfile", {})
                for key in ("owner", "group"):
                    line.pop(key, None)
                if line:
                    line["line"] = line["line"].replace("'php-string.j2'", repr(str(template)))
            return tasks

        result = run_hook("mautic", "replaced", {
            "restore_target": "mautic",
            "homelabinfra_infra": {"domain": "example.invalid"},
            "app_config": {"routing": {"subdomain": "crm"}},
            "_mautic_restore_data_path": str(data),
            "_mautic_restore_database_backend": {"host": "db.example.invalid", "port": 3306},
            "_mautic_restore_database_name": "mautic",
            "_mautic_restore_database_user": "mautic",
            "_mautic_restore_database_password": "it's 50% a\\b",
        }, stub)
        assert result.returncode == 0, result.stdout + result.stderr
        config = (data / "config/local.php").read_text()
        for line in ["'db_host' => 'db.example.invalid',", "'db_port' => '3306',",
                     "'db_password' => 'it\\'s 50%% a\\\\b',",
                     "'site_url' => 'https://crm.example.invalid',",
                     "'default_timezone' => 'Pacific/Honolulu',"]:
            assert line in config, (line, config)
        assert "source-db" not in config and config.rstrip().endswith(");")
        assert (data / "config/local.php").stat().st_mode & 0o777 == 0o640


def static_contract():
    config = defaults["mautic_defaults"]
    assert config["hosting"] == {
        "method": "docker", "supported": ["docker-vm", "kubernetes"], "impossible": {}}
    assert config["recovery"]["methods"] == ["native"]
    assert config["backup"]["application_consistent"] is True
    assert {"backup", "restore"} <= set(catalog["applications"]["mautic"]["actions"])
    for transport in ["EMAIL", "HIT", "FAILED"]:
        assert f"MAUTIC_MESSENGER_DSN_{transport}: doctrine://default" in compose_template

    deploy_tasks = list(tasks_recursive(deploy_source))
    seed_config = next(task for task in deploy_tasks
                       if task.get("name") == "Mautic | Seed configuration on first deploy")
    converge_config = next(task for task in deploy_tasks
                           if task.get("name") == "Mautic | Converge platform-owned configuration fields")
    for config_task, module in [(seed_config, "ansible.builtin.template"),
                                (converge_config, "ansible.builtin.lineinfile")]:
        assert config_task[module]["owner"] == "www-data"
        assert config_task[module]["group"] == "www-data"
        assert config_task[module]["mode"] == "0640"
    deploy_names = [task.get("name") for task in deploy_tasks]
    start_web = next(task for task in deploy_tasks
                     if task.get("name") == "Mautic | Start web service")
    verify_runtime = next(task for task in deploy_tasks
                          if task.get("name") == "Mautic | Verify the web runtime can access its configuration")
    wait_health = next(task for task in deploy_tasks
                       if task.get("name") == "Mautic | Wait for health endpoint")
    start_background = next(task for task in deploy_tasks
                            if task.get("name") == "Mautic | Start background services")
    store_credentials = next(task for task in deploy_tasks
                             if task.get("name") == "Mautic | Store credentials in the canonical Vaultwarden item")
    assert start_web["community.docker.docker_compose_v2"]["services"] == ["mautic_web"]
    assert store_credentials.get("when") == "not ansible_check_mode"
    runtime_check = " ".join(verify_runtime["ansible.builtin.command"]["argv"])
    assert "--user www-data" in runtime_check
    assert "test -r /var/www/html/config/local.php" in runtime_check
    assert "test -w /var/www/html/config/local.php" in runtime_check
    assert start_background["community.docker.docker_compose_v2"]["services"] == [
        "mautic_cron", "mautic_worker"
    ]
    assert deploy_names.index(verify_runtime["name"]) < deploy_names.index(wait_health["name"])
    assert deploy_names.index(wait_health["name"]) < deploy_names.index(start_background["name"])

    assert config["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"
    upload = next(task for task in tasks_recursive(backup_source)
                  if task.get("ansible.builtin.include_tasks", "").endswith("pbs-client-backup.yml"))
    assert upload["vars"]["pbs_client_stop_services"] == ["mautic_web", "mautic_cron", "mautic_worker"]
    assert upload["vars"]["pbs_client_quiesced_hook"] == "{{ role_path }}/tasks/backup-quiesced.yml"
    assert upload["vars"]["pbs_client_keep_daily"] == "", "Mautic keeps only the two newest points"
    assert upload["vars"]["pbs_client_backup_args"] == ["--all-file-systems", "true"]
    assert upload["vars"]["pbs_client_backup_mounts"] == [{
        "path": "{{ _mautic_backup_data_path }}", "target": "{{ _mautic_backup_root }}/mautic"}]
    hook = yaml.safe_load((repo / "ansible/roles/mautic/tasks/backup-quiesced.yml").read_text())
    assert hook[0]["ansible.builtin.include_tasks"] == "../../../tasks/database/dump.yml"
    assert hook[0]["vars"]["database_dump_file"] == "{{ _mautic_backup_root }}/database/mautic.sql"
    assert "mautic_config_file" in str(backup_source)
    assert "mautic_media_files_dir" in str(backup_source)
    assert "mautic_media_images_dir" in str(backup_source)

    [restore_config] = [task for task in replaced_source if "ansible.builtin.lineinfile" in task]
    deploy_config = next(task for task in deploy_source
                         if task.get("name") == "Mautic | Converge platform-owned configuration fields")
    assert restore_config["ansible.builtin.lineinfile"]["line"] == \
        deploy_config["ansible.builtin.lineinfile"]["line"], "Deploy and restore write local.php alike"
    assert restore_config["ansible.builtin.lineinfile"]["owner"] == "www-data"
    assert restore_config["ansible.builtin.lineinfile"]["group"] == "www-data"

    assert any(task.get("name") == "Mautic restore | Preview or replace the file tree and database"
               for task in restore_source)
    assert restore_config["ansible.builtin.lineinfile"]["mode"] == "0640"


def main():
    static_contract()
    check_restore_swap()
    print("Mautic: one-archive recovery, restore swap inputs and hooks passed")


if __name__ == "__main__":
    main()
