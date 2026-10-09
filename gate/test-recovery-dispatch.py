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
    for inputs in ({"instance": "actual-budget"},
                   {"instance": "actual-budget-copy", "app": "actual-budget"}):
        check("backup-app.yml", "Publish the application backup dispatch across plays",
              inputs, {"_ba_app": "actual-budget"})
    check("restore-app.yml", "Publish the application restore dispatch across plays",
          {"instance": "actual-budget", "snapshot": "host/actual-budget/2026-10-08T00:00:00Z"},
          {"_ra_app": "actual-budget", "_ra_target": "actual-budget", "_ra_method": "",
           "_ra_destination": "existing", "_ra_recovery_point": "host/actual-budget/2026-10-08T00:00:00Z",
           "_ra_overwrite": False, "_ra_pre_restore_point": ""})
    check("restore-app.yml", "Publish the application restore dispatch across plays",
          {"instance": "actual-budget", "app": "actual-budget", "target": "actual-budget-copy",
           "method": "native", "destination": "existing", "overwrite": True,
           "recovery_point": "selected", "snapshot": "ignored", "pre_restore_point": "independent"},
          {"_ra_app": "actual-budget", "_ra_target": "actual-budget-copy", "_ra_method": "native",
           "_ra_destination": "existing", "_ra_recovery_point": "selected",
           "_ra_overwrite": True, "_ra_pre_restore_point": "independent"})
    # Exercise the role's actual PBS resolver include without live access. An absent
    # PBS must reach the role's safety assertion, rather than a missing include path.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pbs-dispatch.yml"
        tasks = []
        for application in ["actual-budget", "plex"]:
          for operation in ["backup", "restore"]:
            tasks.append({
                "block": [{"ansible.builtin.include_role": {
                    "name": application, "tasks_from": operation}}],
                "rescue": [{"ansible.builtin.assert": {"that": [
                    "ansible_failed_result.msg is search('No usable PBS')",
                    "not k8s_pbs_available"]}}],
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
