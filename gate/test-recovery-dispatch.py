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
        for app in ["qbittorrent", "bazarr", "sabnzbd", "deemix"]:
            defaults = yaml.safe_load((repo / f"ansible/vars/app-defaults/{app}.yml").read_text())[f"{app}_defaults"]
            tasks.append({
                "vars": {"recovery_app_config": defaults, "recovery_app": app,
                         "recovery_instance": app, "recovery_operation": "restore"},
                "block": [
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                ],
            })
        for app in ["qbittorrent", "actual-budget", "plex", "sabnzbd", "bazarr", "deemix", "tautulli", "servarr"]:
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
                "app_config": {"backup": {"enabled": True, "application_consistent": True}},
                "restore_overwrite": False,
            }, "tasks": tasks,
        }]))
        env = os.environ | {"ANSIBLE_STDOUT_CALLBACK": "default", "ANSIBLE_NOCOLOR": "1",
                            "ANSIBLE_ROLES_PATH": str(repo / "ansible/roles")}
        result = subprocess.run([str(ansible), "-i", "localhost,", "-c", "local", str(path)],
                                env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
    print("Recovery: backup/restore input publication across plays passed")


if __name__ == "__main__":
    main()
