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
    # Render the production Docker restore arguments in a second play. A renamed
    # target must receive its own config path, while an authored custom path survives.
    source = yaml.safe_load((repo / "ansible/playbooks/maintenance/restore-app.yml").read_text())
    dispatch = next(play for play in source if play["name"] ==
                    "Restore App | Restore Docker application data from PBS")["tasks"][0]
    for app in ["sabnzbd", "bazarr", "tautulli", "prowlarr", "sonarr", "radarr", "lidarr"]:
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
    for app in ["actual-budget", "sabnzbd", "bazarr", "tautulli"]:
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
        for app in ["bazarr", "sabnzbd"]:
            defaults = yaml.safe_load((repo / f"ansible/vars/app-defaults/{app}.yml").read_text())[f"{app}_defaults"]
            tasks.append({
                "vars": {"recovery_app_config": defaults, "recovery_app": app,
                         "recovery_instance": app, "recovery_operation": "restore"},
                "block": [
                    {"ansible.builtin.include_tasks": str(repo / "ansible/tasks/recovery/resolve-method.yml")},
                    {"ansible.builtin.assert": {"that": ["recovery_method_resolved == 'native'"]}},
                ],
            })
        for app in ["actual-budget", "plex", "sabnzbd", "bazarr", "tautulli", "servarr"]:
            for operation in ["backup", "restore"]:
                tasks.append({
                    "block": [{"ansible.builtin.include_role": {
                        "name": app, "tasks_from": operation}},
                        {"ansible.builtin.fail": {"msg": "Missing PBS was accepted"}}],
                    "rescue": [{"ansible.builtin.assert": {"that": [
                        "ansible_failed_result.msg is search('No usable PBS')",
                        "not k8s_pbs_available"]}}],
                })
        tasks.append({
            "vars": {"restore_overwrite": True, "restore_snapshot": ""},
            "block": [{"ansible.builtin.include_role": {
                "name": "tautulli", "tasks_from": "restore"}},
                {"ansible.builtin.fail": {"msg": "Overwrite without a point was accepted"}}],
            "rescue": [{"ansible.builtin.assert": {"that": [
                "ansible_failed_result.msg is search('Tautulli restore needs')"]}}],
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
