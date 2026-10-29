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


def check_native_restore_storage_scopes():
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance/restore-app.yml").read_text())[0]
    scope_task = next(task for task in source["pre_tasks"]
                      if task.get("name") == "Resolve native guest storage identity scopes")
    identity_task = next(task for task in source["pre_tasks"]
                         if task.get("name") == "Resolve target identities before mutation")
    storage_key_task = next(task for task in source["pre_tasks"]
                            if task.get("name") == "Scope native guest paths to their owning guest")
    provision_task = next(task for task in source["pre_tasks"]
                          if task.get("name") == "Provision the isolated new restore target")
    provision_argv = provision_task["ansible.builtin.command"]["argv"]
    nested_ansible = provision_argv[0]
    assert nested_ansible == "{{ ansible_playbook_python | dirname }}/ansible-playbook"
    assert provision_task["environment"]["ANSIBLE_CONFIG"] == (
        "{{ playbook_dir }}/../../ansible.cfg"
    )
    assert provision_argv[1:4] == [
        "-i", "{{ playbook_dir }}/../../inventory/",
        "{{ playbook_dir }}/../apps/{{ _ra_app }}.yml",
    ]
    assert (repo / "ansible/playbooks/maintenance/../../inventory").is_dir()
    assert (repo / "ansible/playbooks/maintenance/../apps/mariadb.yml").is_file()
    cases = [
        # The same guest-local path belongs to separate root filesystems when a native
        # database is staged as another Proxmox guest.
        ({"_ra_kind": "native", "_ra_app": "mariadb", "_ra_source_instance": "db-source",
          "_ra_target": "db-stage",
          "app_config": {"proxmox": {"guest_type": "lxc"},
                         "app": {"data_path": "/var/lib/mysql"}},
          "restore_target_config": {"proxmox": {"guest_type": "vm"},
                                    "app": {"data_path": "/var/lib/mysql"}}},
         "_ra_source_storage_key != _ra_target_storage_key"),
        # Host-mounted paths remain shared and must still trip the collision guard.
        ({"_ra_kind": "native", "_ra_app": "mariadb", "_ra_source_instance": "db-source",
          "_ra_target": "db-stage",
          "app_config": {"proxmox": {"guest_type": "lxc"},
                         "media_storage": {"mounts": [{"host": "/srv/db", "path": "/var/lib/mysql"}]},
                         "app": {"data_path": "/var/lib/mysql"}},
          "restore_target_config": {"proxmox": {"guest_type": "vm"},
                                    "media_storage": {"mounts": [{"host": "/srv/db", "path": "/var/lib/mysql"}]},
                                    "app": {"data_path": "/var/lib/mysql"}}},
         "_ra_source_storage_key == _ra_target_storage_key"),
        # Replacing a native guest in place continues to be treated as a collision.
        ({"_ra_kind": "native", "_ra_app": "mariadb", "_ra_source_instance": "db-source",
          "_ra_target": "db-source",
          "app_config": {"proxmox": {"guest_type": "lxc"},
                         "app": {"data_path": "/var/lib/mysql"}},
          "restore_target_config": {"proxmox": {"guest_type": "lxc"},
                                    "app": {"data_path": "/var/lib/mysql"}}},
         "_ra_source_storage_key == _ra_target_storage_key"),
    ]
    plays = [{"name": f"Check native restore storage scope {index}", "hosts": "localhost",
              "gather_facts": False, "vars": inputs,
              "tasks": [scope_task, identity_task, storage_key_task,
                        {"ansible.builtin.assert": {"that": [assertion]}}]}
             for index, (inputs, assertion) in enumerate(cases)]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "native-restore-storage-scope.yml"
        path.write_text(yaml.safe_dump(plays))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    # Keep the nested provisioning process runnable when command PATH omits the venv,
    # and prove it loads the candidate's custom inventory plugin path without relying
    # on an inherited ANSIBLE_CONFIG from the parent process.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "nested-ansible-path.yml"
        nested_config = nested_ansible.replace("ansible-playbook", "ansible-config")
        path.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False, "connection": "local",
            "tasks": [
                {"ansible.builtin.command": {
                    "argv": [nested_config, "dump", "--only-changed"]},
                 "environment": {"ANSIBLE_CONFIG": str(repo / "ansible/ansible.cfg")},
                 "register": "nested_ansible_result", "changed_when": False},
                {"ansible.builtin.assert": {"that": [
                    "nested_ansible_result.rc == 0",
                    "nested_ansible_result.stdout is search('DEFAULT_INVENTORY_PLUGIN_PATH')",
                    f"nested_ansible_result.stdout is search({json.dumps(str(repo / 'ansible/inventory_plugins'))})",
                ]}},
            ],
        }]))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                            "PATH": "/usr/bin:/bin"}
        env.pop("ANSIBLE_CONFIG", None)
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def check(playbook, task_name, inputs, expected):
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance" / playbook).read_text())[0]
    publish = next(task for task in source["pre_tasks"] if task["name"] == task_name)
    # Play two deliberately receives none of play one's vars. This is the app recovery
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


def check_iso_appliance_recovery_dispatch():
    for app in ["opnsense", "proxmox-datacenter-manager"]:
        defaults_key = app.replace("-", "_") + "_defaults"
        defaults_path = repo / f"ansible/vars/app-defaults/{app}.yml"
        app_config = yaml.safe_load(defaults_path.read_text())[defaults_key]

        for playbook, kind_fact in [
            ("backup-app.yml", "_ba_kind"),
            ("restore-app.yml", "_ra_kind"),
        ]:
            source = yaml.safe_load(
                (repo / "ansible/playbooks/maintenance" / playbook).read_text()
            )[0]
            resolve_kind = next(
                task for task in source["pre_tasks"]
                if task.get("name") == "Resolve the hosting kind"
            )
            plays = [{
                "name": f"Resolve {app} {playbook} hosting kind",
                "hosts": "localhost",
                "gather_facts": False,
                "connection": "local",
                "vars": {
                    "app_config": app_config,
                    "homelabinfra_infra": {"databases": {}},
                    "instance": app,
                },
                "tasks": [
                    resolve_kind,
                    {"ansible.builtin.assert": {
                        "that": [f"{kind_fact} == 'appliance'"]
                    }},
                ],
            }]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "appliance-recovery-dispatch.yml"
                path.write_text(yaml.safe_dump(plays))
                env = os.environ | {
                    "ANSIBLE_STDOUT_CALLBACK": "default",
                    "ANSIBLE_NOCOLOR": "1",
                }
                result = subprocess.run(
                    [str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                    env=env, text=True, capture_output=True, check=False,
                )
                assert result.returncode == 0, result.stdout + result.stderr


def main():
    check_iso_appliance_recovery_dispatch()
    check_native_restore_storage_scopes()

    # Role metadata dependencies run before the selected backup/restore task file.
    # The Docker dependency consumes host architecture facts, so both app target plays
    # must gather facts before Ansible dispatches into an application role.
    for playbook, play_name in [
        ("backup-app.yml", "Backup App | Run the application-owned backup"),
        ("restore-app.yml", "Restore App | Restore Docker or native-LXC application data from PBS"),
    ]:
        plays = yaml.safe_load(
            (repo / "ansible/playbooks/maintenance" / playbook).read_text()
        )
        target_play = next(play for play in plays if play.get("name") == play_name)
        assert target_play.get("gather_facts") is True, play_name

    backup_tasks = yaml.safe_load((repo / "ansible/roles/immich/tasks/backup.yml").read_text())
    archive_block = next(item for item in backup_tasks
                         if item.get("name") == "Immich backup | Archive the database and media to PBS")
    upload = archive_block["block"][0]
    assert upload["ansible.builtin.include_tasks"] == "../../../tasks/backup/pbs-client-backup.yml"
    assert upload["vars"]["pbs_client_stop_services"] == ["immich-server", "immich-machine-learning"]
    assert [a["name"] for a in upload["vars"]["pbs_client_archives"]] == ["database.pxar", "media.pxar"]
    hook = yaml.safe_load((repo / "ansible/roles/immich/tasks/backup-quiesced.yml").read_text())
    assert hook[0]["ansible.builtin.include_tasks"] == "../../../tasks/database/dump.yml"
    assert hook[0]["vars"]["database_dump_file"] == "{{ _immich_backup_scratch.path }}/immich.dump"

    restore_tasks = yaml.safe_load((repo / "ansible/roles/immich/tasks/restore.yml").read_text())
    restore_contract = next(task for task in restore_tasks
                            if task.get("name") == "Immich restore | Assert the restore contract")
    assert "_immich_restore_media_path | length > 1" in restore_contract[
        "ansible.builtin.assert"]["that"]
    assert any("_immich_restore_media_path.split('/')" in condition
               for condition in restore_contract["ansible.builtin.assert"]["that"])
    assert "app_config.media_storage.owner.uid | default(0) | int > 0" in restore_contract[
        "ansible.builtin.assert"]["that"]
    assert "app_config.media_storage.owner.gid | default(0) | int > 0" in restore_contract[
        "ansible.builtin.assert"]["that"]
    restore_mountpoint = next(task for task in restore_tasks
                              if task.get("name") == "Immich restore | Check the declared media path is an exact mount point")
    assert restore_mountpoint["ansible.builtin.command"]["argv"] == [
        "findmnt", "--noheadings", "--output", "TARGET", "--mountpoint",
        "{{ _immich_restore_media_path }}",
    ]
    assert restore_mountpoint["when"] == "restore_overwrite | bool"
    assert restore_mountpoint["no_log"] is True
    restore_mount_guard = next(task for task in restore_tasks
                               if task.get("name") == "Immich restore | Refuse to replace an unmounted media path")
    assert restore_mount_guard["when"] == "restore_overwrite | bool"
    assert "_immich_restore_media_mountpoint.stdout | trim == _immich_restore_media_path" in (
        restore_mount_guard["ansible.builtin.assert"]["that"])
    selected = next(task["block"] for task in restore_tasks
                    if task.get("name") == "Immich restore | Restore the selected point")
    names = [task.get("name") for task in selected]
    fetch = selected[names.index("Immich restore | Fetch the database and durable media archives")]
    assert fetch["ansible.builtin.include_tasks"] == "../../../tasks/backup/pbs-client-restore.yml"
    assert fetch["vars"]["pbs_client_restore_args"] == ["--allow-existing-dirs", "true"]
    assert [a["name"] for a in fetch["vars"]["pbs_client_archives"]] == ["database.pxar", "media.pxar"]
    replace_index = names.index("Immich restore | Replace the application-consistent artifact")
    assert names.index("Immich restore | Fetch the database and durable media archives") < names.index(
        "Immich restore | Refuse an archive without a database dump") < replace_index, (
        "both archives must be fetched and the dump checked before Immich stops")
    restore_block = selected[replace_index]["block"]
    assert restore_block[0]["name"] == "Immich restore | Stop Immich services"
    fetch_index = 0
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
        "find", ".", "-mindepth", "1", "-maxdepth", "1",
        "-exec", "rm", "-rf", "--", "{}", "+",
    ], clear_argv
    assert clear_media["ansible.builtin.command"]["chdir"] == "{{ _immich_restore_media_path }}"
    assert clear_media.get("no_log") is True
    assert clear_media.get("changed_when") is True
    copy_media = restore_block[copy_index]
    copy_argv = copy_media["ansible.builtin.command"]["argv"]
    assert copy_argv == [
        "setpriv", "--reuid", "{{ app_config.media_storage.owner.uid | int }}",
        "--regid", "{{ app_config.media_storage.owner.gid | int }}", "--clear-groups",
        "cp", "-a", "--no-preserve=ownership", "{{ _immich_restore_scratch.path }}/media/.",
        ".",
    ], copy_argv
    assert copy_media["ansible.builtin.command"]["chdir"] == "{{ _immich_restore_media_path }}"
    assert copy_media.get("no_log") is True
    assert copy_media.get("changed_when") is True

    # Render the production Docker restore arguments in a second play. A renamed
    # target must receive its own config path, while an authored custom path survives.
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance/restore-app.yml").read_text())
    dispatch = next(play for play in source if play["name"] ==
                    "Restore App | Restore Docker or native-LXC application data from PBS")["tasks"][0]
    for app in ["qbittorrent", "sabnzbd", "bazarr", "deemix", "tautulli", "prowlarr", "sonarr", "radarr", "lidarr", "frigate"]:
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
    for data_path, expected_path in [
        ("/opt/observability/data", "/opt/observability-copy/data"),
        ("/srv/custom/observability-data", "/srv/custom/observability-data"),
    ]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "target-data-path.yml"
            path.write_text(yaml.safe_dump([
                {"hosts": "localhost", "gather_facts": False, "tasks": [
                    {"ansible.builtin.set_fact": {
                        "_ra_app": "observability", "_ra_source_instance": "observability",
                        "_ra_target": "observability-copy",
                        "restore_target_config": {"app": {"data_path": data_path}}}}]},
                {"hosts": "localhost", "gather_facts": False, "tasks": [
                    {"vars": dispatch["vars"], "ansible.builtin.assert": {"that": [
                        "instance == 'observability-copy'",
                        f"app_config.app.data_path == '{expected_path}'"]}}]},
            ]))
            env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1"}
            result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                    env=env, text=True, capture_output=True, check=False)
            assert result.returncode == 0, result.stdout + result.stderr
    for app in ["qbittorrent", "actual-budget", "sabnzbd", "bazarr", "deemix", "tautulli", "observability", "frigate", "uptime-kuma"]:
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
        for app in ["qbittorrent", "bazarr", "sabnzbd", "deemix", "karakeep", "frigate", "uptime-kuma"]:
            defaults_key = "uptime_kuma_defaults" if app == "uptime-kuma" else f"{app}_defaults"
            defaults = yaml.safe_load((repo / f"ansible/vars/app-defaults/{app}.yml").read_text())[defaults_key]
            tasks.append({
                "vars": {"recovery_app_config": defaults, "recovery_app": app,
                         "recovery_instance": app, "recovery_operation": "restore"},
                "block": [
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                ],
            })
        # Roles on tasks/backup/pbs-client-*.yml get this check from gate/test-pbs-client.py.
        for app in ["observability"]:
            for operation in ["backup", "restore"]:
                tasks.append({
                    "block": [{"ansible.builtin.include_role": {
                        "name": app, "tasks_from": operation}},
                        {"ansible.builtin.fail": {"msg": "Missing PBS was accepted"}}],
                    "rescue": [{"ansible.builtin.assert": {"that": [
                        "ansible_failed_result.msg is search('No usable PBS')",
                        "not k8s_pbs_available"]}}],
                })
        for app in ["tautulli", "deemix", "observability", "uptime-kuma"]:
            tasks.append({
                "vars": {"restore_overwrite": True, "restore_snapshot": ""},
                "block": [{"ansible.builtin.include_role": {
                    "name": app, "tasks_from": "restore"}},
                    {"ansible.builtin.fail": {"msg": "Overwrite without a point was accepted"}}],
                "rescue": [{"ansible.builtin.assert": {"that": [
                        f"ansible_failed_result.msg is search('{('Uptime Kuma' if app == 'uptime-kuma' else app.capitalize())} restore needs')"]}}],
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
    # stack-host app name.
    removal_plays = []
    for app_name in ("karakeep", "plane"):
        host = f"remove_test_{app_name}"
        identity_assertions = [
            f"hostvars['localhost']._rm_app_name == {json.dumps(app_name)}",
        ]
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
                    *identity_assertions,
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

    immich_remove_inputs = next(
        task for task in unwire_play["pre_tasks"]
        if task.get("name") == "Resolve Immich's media removal inputs"
    )
    immich_validate = next(
        task for task in unwire_play["pre_tasks"]
        if task.get("name") == "Validate Immich's dedicated media mount"
    )
    assert "_rm_immich_media_mounts | length == 1" in immich_validate[
        "ansible.builtin.assert"]["that"]
    assert "_rm_immich_media_path | length > 1" in immich_validate[
        "ansible.builtin.assert"]["that"]
    assert any("_rm_immich_media_path.split('/')" in condition
               for condition in immich_validate["ansible.builtin.assert"]["that"])
    assert immich_remove_inputs["no_log"] is True

    compose_block = next(
        task["block"] for task in remove_playbook[1]["tasks"]
        if task.get("name") == "Remove the Compose project"
    )
    immich_include = next(
        (index, task) for index, task in enumerate(compose_block)
        if task.get("name") == "Remove Immich's durable media"
    )
    stop_compose = next(
        index for index, task in enumerate(compose_block)
        if task.get("name") == "Stop and remove the containers"
    )
    assert immich_include[0] > stop_compose
    assert immich_include[1]["ansible.builtin.include_tasks"] == "../../roles/immich/tasks/remove.yml"
    assert "_rm_delete_data | bool" in immich_include[1]["when"]

    immich_remove_tasks = yaml.safe_load(
        (repo / "ansible/roles/immich/tasks/remove.yml").read_text()
    )
    exact_mount = next(
        task for task in immich_remove_tasks
        if task.get("name") == "Immich remove | Require the declared media path to be an exact mount point"
    )
    assert exact_mount["ansible.builtin.command"]["argv"] == [
        "findmnt", "--noheadings", "--output", "TARGET", "--mountpoint",
        "{{ hostvars['localhost']._rm_immich_media_path }}",
    ]
    assert not any("DROP" in str(task) for task in immich_remove_tasks)
    clear_media = next(
        task for task in immich_remove_tasks
        if task.get("name") == "Immich remove | Empty its durable media mount as the declared owner"
    )
    assert clear_media["ansible.builtin.command"]["argv"][:7] == [
        "setpriv", "--reuid", "{{ hostvars['localhost'].app_config.media_storage.owner.uid | int }}",
        "--regid", "{{ hostvars['localhost'].app_config.media_storage.owner.gid | int }}",
        "--clear-groups", "find",
    ]
    assert "-mindepth" in clear_media["ansible.builtin.command"]["argv"]
    assert "-maxdepth" in clear_media["ansible.builtin.command"]["argv"]
    assert clear_media["args"]["chdir"] == "{{ hostvars['localhost']._rm_immich_media_path }}"
    assert clear_media["ansible.builtin.command"]["argv"][7] == "."
    assert clear_media["no_log"] is True
    find_media = next(
        task for task in immich_remove_tasks
        if task.get("name") == "Immich remove | Find existing media contents as the declared owner"
    )
    assert find_media["args"]["chdir"] == "{{ hostvars['localhost']._rm_immich_media_path }}"
    assert find_media["ansible.builtin.command"]["argv"][7] == "."
    assert find_media["no_log"] is True
    print("Recovery: backup/restore dispatch and named database removal safety passed")


if __name__ == "__main__":
    main()
