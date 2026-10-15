#!/usr/bin/env python3
"""Exercise production recovery input publication through two real Ansible plays."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

repo = Path(__file__).resolve().parents[1]
ansible = Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"


def check(playbook, task_name, inputs, expected):
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance" / playbook).read_text())[0]
    publish = next(task for task in source["pre_tasks"] if task["name"] == task_name)
    # Play two deliberately receives none of play one's vars. This is the Docker
    # dispatch boundary that syntax checks and single-play templating cannot exercise.
    plays = [
        {"name": "Resolve recovery inputs", "hosts": "localhost", "gather_facts": False,
         "vars": source["vars"] | inputs, "tasks": [publish]},
        {"name": "Read recovery inputs from another play", "hosts": "localhost",
         "gather_facts": False, "tasks": [{"ansible.builtin.assert": {
             "that": [f"hostvars['localhost'][{json.dumps(key)}] == {json.dumps(value)}"
                      for key, value in expected.items()]}}]},
    ]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "dispatch.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def main():
    # Role includes resolve from the calling maintenance playbook directory. Keep the
    # Immich backup and restore roles pointed at the shared PBS resolver from that root.
    maintenance_dir = repo / "ansible/playbooks/maintenance"
    pbs_resolver = repo / "ansible/tasks/backup/resolve-pbs-target.yml"
    for task_file in ["backup.yml", "restore.yml"]:
        tasks = yaml.safe_load((repo / "ansible/roles/immich/tasks" / task_file).read_text())
        resolver_task = next(task for task in tasks
                             if task.get("name", "").endswith("| Resolve the PBS client target"))
        resolved = (maintenance_dir / resolver_task["ansible.builtin.include_tasks"]).resolve()
        assert resolved == pbs_resolver.resolve(), (task_file, resolved)

    backup_tasks = yaml.safe_load((repo / "ansible/roles/immich/tasks/backup.yml").read_text())
    archive_block = next(item for item in backup_tasks
                         if item.get("name") == "Immich backup | Archive the database and media to PBS")
    for task_name in ["Immich backup | Dump the named PostgreSQL database",
                      "Immich backup | Push the database and durable media archives"]:
        task = next(item for item in archive_block["block"] if item.get("name") == task_name)
        assert task.get("environment"), task_name
        assert task.get("no_log") is True, task_name
        assert task.get("changed_when") is True, task_name
        assert not {"environment", "no_log", "changed_when"} & set(
            task["ansible.builtin.command"]), task_name

    pbs_push = next(item for item in archive_block["block"]
                    if item.get("name") == "Immich backup | Push the database and durable media archives")
    pbs_script = pbs_push["ansible.builtin.command"]["argv"][-1]
    assert "proxmox-backup-client backup database.pxar:/backup media.pxar:/media" in pbs_script
    assert '--backup-id "$BACKUP_ID"' in pbs_script
    assert "BACKUP_ID" in pbs_push["environment"]

    restore_tasks = yaml.safe_load((repo / "ansible/roles/immich/tasks/restore.yml").read_text())
    restore_contract = next(task for task in restore_tasks
                            if task.get("name") == "Immich restore | Assert the restore contract")
    assert "app_config.media_storage.owner.uid | default(0) | int > 0" in restore_contract[
        "ansible.builtin.assert"]["that"]
    assert "app_config.media_storage.owner.gid | default(0) | int > 0" in restore_contract[
        "ansible.builtin.assert"]["that"]
    restore_block = next(task["block"] for task in restore_tasks
                         if task.get("name") == "Immich restore | Replace the application-consistent artifact")
    fetch_index = next(i for i, task in enumerate(restore_block)
                       if task.get("name") == "Immich restore | Fetch the database and durable media archives")
    database_private_index = next(i for i, task in enumerate(restore_block)
                                  if task.get("name") == "Immich restore | Keep the database dump private from the media owner")
    scratch_traverse_index = next(i for i, task in enumerate(restore_block)
                                  if task.get("name") == "Immich restore | Allow the media owner to traverse private scratch")
    media_owner_index = next(i for i, task in enumerate(restore_block)
                             if task.get("name") == "Immich restore | Assign extracted media to its declared owner")
    clear_index = next(i for i, task in enumerate(restore_block)
                       if task.get("name") == "Immich restore | Empty the durable media mount as its declared owner")
    copy_index = next(i for i, task in enumerate(restore_block)
                      if task.get("name") == "Immich restore | Restore durable originals and thumbnails as their declared owner")
    assert fetch_index < database_private_index < scratch_traverse_index < media_owner_index < clear_index < copy_index, (
        "both PBS archives must validate before staging changes or the target mount is replaced")
    database_private = restore_block[database_private_index]
    assert database_private["ansible.builtin.file"]["mode"] == "0700"
    assert database_private["ansible.builtin.file"]["owner"] == "root"
    assert database_private.get("no_log") is True
    scratch_traverse = restore_block[scratch_traverse_index]
    assert scratch_traverse["ansible.builtin.file"]["mode"] == "0711"
    assert scratch_traverse.get("no_log") is True
    media_stage = restore_block[media_owner_index]["ansible.builtin.file"]
    assert media_stage["owner"] == "{{ app_config.media_storage.owner.uid | int }}"
    assert media_stage["group"] == "{{ app_config.media_storage.owner.gid | int }}"
    assert media_stage["recurse"] is True
    assert restore_block[media_owner_index].get("no_log") is True
    clear_media = restore_block[clear_index]
    clear_argv = clear_media["ansible.builtin.command"]["argv"]
    assert clear_argv == [
        "setpriv", "--reuid", "{{ app_config.media_storage.owner.uid | int }}",
        "--regid", "{{ app_config.media_storage.owner.gid | int }}", "--clear-groups",
        "find", "{{ _immich_restore_media_path }}", "-mindepth", "1", "-maxdepth", "1",
        "-exec", "rm", "-rf", "--", "{}", "+",
    ], clear_argv
    assert clear_media["ansible.builtin.command"]["chdir"] == "/"
    assert clear_media.get("no_log") is True
    assert clear_media.get("changed_when") is True
    copy_media = restore_block[copy_index]
    copy_argv = copy_media["ansible.builtin.command"]["argv"]
    assert copy_argv == [
        "setpriv", "--reuid", "{{ app_config.media_storage.owner.uid | int }}",
        "--regid", "{{ app_config.media_storage.owner.gid | int }}", "--clear-groups",
        "cp", "-a", "--no-preserve=ownership", "{{ _immich_restore_scratch.path }}/media/.",
        "{{ _immich_restore_media_path }}/",
    ], copy_argv
    assert copy_media["ansible.builtin.command"]["chdir"] == "/"
    assert copy_media.get("no_log") is True
    assert copy_media.get("changed_when") is True

    # Render the production Docker restore arguments in a second play. A renamed
    # target must receive its own config path, while an authored custom path survives.
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance/restore-app.yml").read_text())
    dispatch = next(play for play in source if play["name"] ==
                    "Restore App | Restore Docker application data from PBS")["tasks"][0]
    for app in ["qbittorrent", "sabnzbd", "bazarr", "deemix", "tautulli", "prowlarr", "sonarr", "radarr", "lidarr"]:
        for config_path, expected_path in [(f"/opt/{app}/config", f"/opt/{app}-copy/config"),
                                           ("/srv/custom/config", "/srv/custom/config")]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "target-path.yml"
                path.write_text(yaml.safe_dump([
                    {"hosts": "localhost", "gather_facts": False, "tasks": [
                        {"ansible.builtin.set_fact": {
                            "_ra_app": app, "_ra_source_instance": app,
                            "_ra_target": app + "-copy",
                            "restore_target_config": {"app": {"config_path": config_path}}}}]},
                    {"hosts": "localhost", "gather_facts": False, "tasks": [
                        {"vars": dispatch["vars"], "ansible.builtin.assert": {"that": [
                            f"instance == '{app}-copy'",
                            f"app_config.app.config_path == '{expected_path}'"]}}]},
                ]))
                env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
                result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                        env=env, text=True, capture_output=True, check=False)
                assert result.returncode == 0, result.stdout + result.stderr
    for app in ["qbittorrent", "actual-budget", "sabnzbd", "bazarr", "deemix", "tautulli"]:
        for inputs in ({"instance": app}, {"instance": app + "-copy", "app": app}):
            check("backup-app.yml", "Publish the application backup dispatch across plays",
                  inputs, {"_ba_app": app})
        check("restore-app.yml", "Publish the application restore dispatch across plays",
              {"instance": app, "snapshot": f"host/{app}/2026-10-08T00:00:00Z"},
              {"_ra_app": app, "_ra_target": app, "_ra_method": "",
               "_ra_destination": "existing",
               "_ra_recovery_point": f"host/{app}/2026-10-08T00:00:00Z",
               "_ra_overwrite": False, "_ra_pre_restore_point": ""})
        check("restore-app.yml", "Publish the application restore dispatch across plays",
              {"instance": app, "app": app, "target": app + "-copy", "method": "native",
               "overwrite": True, "recovery_point": "selected", "snapshot": "ignored",
               "pre_restore_point": "independent"},
              {"_ra_app": app, "_ra_target": app + "-copy", "_ra_method": "native",
               "_ra_destination": "existing", "_ra_recovery_point": "selected",
               "_ra_overwrite": True, "_ra_pre_restore_point": "independent"})
    # Exercise the role's actual PBS resolver include without live access. An absent
    # PBS must reach the role's safety assertion, rather than a missing include path.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pbs-dispatch.yml"
        tasks = []
        for app in ["qbittorrent", "bazarr", "sabnzbd", "deemix", "karakeep"]:
            defaults = yaml.safe_load((repo / f"ansible/vars/app-defaults/{app}.yml").read_text())[f"{app}_defaults"]
            tasks.append({
                "vars": {"recovery_app_config": defaults, "recovery_app": app,
                         "recovery_instance": app, "recovery_operation": "restore"},
                "block": [
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                ],
            })
        for app in [
            "qbittorrent", "actual-budget", "plex", "sabnzbd", "bazarr",
            "deemix", "tautulli", "servarr", "karakeep",
        ]:
            for operation in ["backup", "restore"]:
                tasks.append({
                    "block": [{"ansible.builtin.include_role": {
                        "name": app, "tasks_from": operation}},
                        {"ansible.builtin.fail": {"msg": "Missing PBS was accepted"}}],
                    "rescue": [{"ansible.builtin.assert": {"that": [
                        "ansible_failed_result.msg is search('No usable PBS')",
                        "not k8s_pbs_available"]}}],
                })
        for app in ["tautulli", "deemix"]:
            tasks.append({
                "vars": {"restore_overwrite": True, "restore_snapshot": ""},
                "block": [{"ansible.builtin.include_role": {
                    "name": app, "tasks_from": "restore"}},
                    {"ansible.builtin.fail": {"msg": "Overwrite without a point was accepted"}}],
                "rescue": [{"ansible.builtin.assert": {"that": [
                    f"ansible_failed_result.msg is search('{app.capitalize()} restore needs')"]}}],
            })

        path.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "vars": {
                "instance": "actual-budget", "homelabinfra_infra": {"backups": {}},
                "app_config": {
                    "app": {"data_path": "/opt/test/data"},
                    "backup": {"enabled": True, "application_consistent": True},
                },
                "karakeep_meilisearch_data_path": "/opt/test/meilisearch",
                "restore_overwrite": False,
            }, "tasks": tasks,
        }]))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                            "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles")}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
    remove_playbook = yaml.safe_load(
        (repo / "ansible/playbooks/apps/remove.yml").read_text()
    )
    unwire_play = next(
        play for play in remove_playbook if play["name"] == "Remove App | Unwire"
    )
    removal_identity = next(
        task for task in unwire_play["tasks"]
        if task.get("name") == "Resolve removal identity for the app host"
    )
    plane_db_inputs = next(
        task for task in unwire_play["pre_tasks"]
        if task.get("name") == "Resolve Plane's named database removal inputs"
    )
    odoo_db_inputs = next(
        task for task in unwire_play["pre_tasks"]
        if task.get("name") == "Resolve Odoo's named database removal inputs"
    )
    identity_facts = removal_identity["ansible.builtin.set_fact"]
    assert identity_facts["_rm_app_name"] == "{{ app | default(instance) }}"
    add_host = next(
        task for task in unwire_play["tasks"]
        if task.get("name") == "Add the app host to the removal group"
    )["ansible.builtin.add_host"]
    assert not {"remove_app", "remove_database_config", "remove_database_backend",
                "remove_database_backend_target"} & set(add_host)

    # Exercise the exact values copied by add_host in another play. They must be plain
    # values after crossing that boundary, so a Karakeep removal cannot inherit a
    # stack-host app name and named-database apps keep their explicit DB target.
    removal_plays = []
    for app_name in ("karakeep", "plane", "odoo"):
        expected_database_config = (
            {"provider": "postgresql", "instance": "dbtest",
             "name": "drill_test", "role": "drill_test"}
            if app_name in ("plane", "odoo") else {}
        )
        expected_database_backend = (
            {"provider": "postgresql", "host": "127.0.0.1", "port": 5432}
            if app_name in ("plane", "odoo") else {}
        )
        host = f"remove_test_{app_name}"
        removal_plays.extend([
            {
                "name": "Resolve and register removal identity",
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {
                    "instance": app_name,
                    "app": app_name,
                    "delete_data": True,
                    "app_config": {"app": {"database": {
                        "provider": "postgresql",
                        "instance": "dbtest",
                        "name": "drill_test",
                        "role": "drill_test",
                    }}},
                    "homelabinfra_infra": {"databases": {"dbtest": {
                        "provider": "postgresql",
                        "host": "127.0.0.1",
                        "port": 5432,
                    }}},
                },
                "tasks": [
                    plane_db_inputs,
                    odoo_db_inputs,
                    removal_identity,
                    {"ansible.builtin.add_host": {
                        "name": host,
                        "groups": f"remove_test_group_{app_name}",
                    }},
                ],
            },
            {
                "name": "Check identity on the removal host",
                "hosts": f"remove_test_group_{app_name}",
                "gather_facts": False,
                "tasks": [{"ansible.builtin.assert": {"that": [
                    f"hostvars['localhost']._rm_app_name == {json.dumps(app_name)}",
                    f"hostvars['localhost']._rm_plane_database_config | default({{}}) == {json.dumps(expected_database_config)}",
                    f"hostvars['localhost']._rm_plane_database_backend | default({{}}) == {json.dumps(expected_database_backend)}",
                    f"hostvars['localhost']._rm_odoo_database_config | default({{}}) == {json.dumps(expected_database_config if app_name == 'odoo' else {})}",
                    f"hostvars['localhost']._rm_odoo_database_backend | default({{}}) == {json.dumps(expected_database_backend if app_name == 'odoo' else {})}",
                ]}}],
            },
        ])
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "remove-identity.yml"
        path.write_text(yaml.safe_dump(removal_plays))
        env = os.environ | {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_NOCOLOR": "1",
        }
        result = subprocess.run(
            [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    compose_remove = next(
        task["block"]
        for task in remove_playbook[1]["tasks"]
        if task.get("name") == "Remove the Compose project"
    )
    plane_schedule = next(
        task for task in compose_remove
        if task.get("name") == "Remove Plane recovery schedule"
    )
    assert plane_schedule["ansible.builtin.cron"]["name"] == (
        "homelab-infra Plane backup {{ instance }}"
    )
    assert plane_schedule["ansible.builtin.cron"]["cron_file"] == (
        "homelab-infra-plane-{{ instance }}"
    )
    assert plane_schedule["ansible.builtin.cron"]["state"] == "absent"
    assert plane_schedule["when"] == "hostvars['localhost']._rm_app_name == 'plane'"

    plane_config = next(
        task for task in compose_remove
        if task.get("name") == "Remove Plane recovery configuration"
    )
    assert plane_config["ansible.builtin.file"]["path"] == (
        "/etc/homelab-infra/plane/{{ instance }}.env"
    )
    assert plane_config["ansible.builtin.file"]["state"] == "absent"
    assert plane_config["when"] == "hostvars['localhost']._rm_app_name == 'plane'"

    remove_pretasks = remove_playbook[0]["pre_tasks"]
    assert plane_db_inputs["when"] == [
        "(app | default(instance)) == 'plane'",
        "delete_data | default(false) | bool",
    ]
    assert odoo_db_inputs["when"] == [
        "(app | default(instance)) == 'odoo'",
        "delete_data | default(false) | bool",
    ]
    plane_db_sharing = next(
        task for task in remove_pretasks
        if task.get("name") == "Check Plane's database and role are not shared by another app"
    )
    sharing_facts = plane_db_sharing["ansible.builtin.set_fact"]
    assert "_rm_plane_shared_database_consumers" in sharing_facts
    assert "_rm_plane_shared_role_consumers" in sharing_facts
    assert plane_db_sharing["no_log"] is True

    odoo_db_sharing = next(
        task for task in remove_pretasks
        if task.get("name") == "Check Odoo's database and role are not shared by another app"
    )
    odoo_sharing_facts = odoo_db_sharing["ansible.builtin.set_fact"]
    assert "_rm_odoo_shared_database_consumers" in odoo_sharing_facts
    assert "_rm_odoo_shared_role_consumers" in odoo_sharing_facts
    assert odoo_db_sharing["no_log"] is True
    odoo_db_target = next(
        task for task in remove_pretasks
        if task.get("name") == "Add Odoo's named database backend to the removal inventory"
    )
    assert odoo_db_target["ansible.builtin.add_host"]["name"] == "odoo_database_removal_target"
    assert odoo_db_target["ansible.builtin.add_host"]["groups"] == "odoo_database_removal_targets"
    assert odoo_db_target["no_log"] is True

    plane_db_remove = next(
        task for task in remove_playbook[1]["tasks"]
        if task.get("name") == "Remove Plane's named database and role"
    )
    assert plane_db_remove["when"] == [
        "remove_kind == 'docker'",
        "hostvars['localhost']._rm_app_name == 'plane'",
        "_rm_delete_data | bool",
    ]
    plane_db_tasks = plane_db_remove["block"]
    owner = next(task for task in plane_db_tasks
                 if task.get("name") == "Read the owner of Plane's named PostgreSQL database")
    assert "pg_get_userbyid" in owner["ansible.builtin.command"]["argv"][-1]
    assert owner["delegate_to"] == "plane_database_removal_target"
    assert owner["no_log"] is True
    owner_guard = next(task for task in plane_db_tasks
                       if task.get("name") == "Refuse to delete Plane's database with an unexpected owner")
    assert "hostvars['localhost']._rm_plane_database_config.role" in owner_guard[
        "ansible.builtin.assert"
    ]["that"][0]
    drop_database = next(task for task in plane_db_tasks
                         if task.get("name") == "Drop Plane's named PostgreSQL database")
    assert "DROP DATABASE {{ hostvars['localhost']._rm_plane_database_config.name }}" in drop_database[
        "ansible.builtin.command"
    ]["argv"][-1]
    assert drop_database["no_log"] is True
    drop_role = next(task for task in plane_db_tasks
                     if task.get("name") == "Drop Plane's named PostgreSQL role")
    assert "DROP ROLE {{ hostvars['localhost']._rm_plane_database_config.role }}" in drop_role[
        "ansible.builtin.command"
    ]["argv"][-1]
    assert drop_role["no_log"] is True

    odoo_db_remove = next(
        task for task in remove_playbook[1]["tasks"]
        if task.get("name") == "Remove Odoo's named database and role"
    )
    assert odoo_db_remove["when"] == [
        "remove_kind == 'docker'",
        "hostvars['localhost']._rm_app_name == 'odoo'",
        "_rm_delete_data | bool",
    ]
    odoo_db_tasks = odoo_db_remove["block"]
    odoo_owner = next(task for task in odoo_db_tasks
                      if task.get("name") == "Read the owner of Odoo's named PostgreSQL database")
    assert "pg_get_userbyid" in odoo_owner["ansible.builtin.command"]["argv"][-1]
    assert odoo_owner["delegate_to"] == "odoo_database_removal_target"
    assert odoo_owner["no_log"] is True
    odoo_owner_guard = next(task for task in odoo_db_tasks
                            if task.get("name") == "Refuse to delete Odoo's database with an unexpected owner")
    assert "hostvars['localhost']._rm_odoo_database_config.role" in odoo_owner_guard[
        "ansible.builtin.assert"]["that"][0]
    odoo_drop_database = next(task for task in odoo_db_tasks
                              if task.get("name") == "Drop Odoo's named PostgreSQL database")
    assert "DROP DATABASE {{ hostvars['localhost']._rm_odoo_database_config.name }}" in odoo_drop_database[
        "ansible.builtin.command"]["argv"][-1]
    assert odoo_drop_database["no_log"] is True
    odoo_drop_role = next(task for task in odoo_db_tasks
                          if task.get("name") == "Drop Odoo's named PostgreSQL role")
    assert "DROP ROLE {{ hostvars['localhost']._rm_odoo_database_config.role }}" in odoo_drop_role[
        "ansible.builtin.command"]["argv"][-1]
    assert odoo_drop_role["no_log"] is True
    print("Recovery: backup/restore dispatch and named database removal safety passed")


if __name__ == "__main__":
    main()
