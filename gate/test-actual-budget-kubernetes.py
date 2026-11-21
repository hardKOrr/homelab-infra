#!/usr/bin/env python3
"""Guard Actual Budget's opt-in Kubernetes workload and shared recovery path.

Checks: Actual Budget's opt-in Kubernetes workload, shared PBS recovery path, and Docker
default.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/actual-budget"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


defaults = yaml.safe_load(read("ansible/vars/app-defaults/actual-budget.yml"))[
    "actual_budget_defaults"
]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == [
    "docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm",
]
assert defaults["hosting"]["impossible"] == {}
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"]["image"]

# The playbook is a shim on playbooks/apps/deploy.yml, which owns the provision, guest,
# cluster and wiring steps (gate/app-matrix.yml records what each method reaches).
playbook = yaml.safe_load(read("ansible/playbooks/apps/actual-budget.yml"))
assert playbook == [{
    "name": "Deploy actual-budget",
    "ansible.builtin.import_playbook": "deploy.yml",
    "vars": {"app": "actual-budget"},
}]

deploy_tasks = yaml.safe_load((ROLE / "tasks/kubernetes.yml").read_text(encoding="utf-8"))
workload = next(
    task for task in deploy_tasks
    if str(task.get("ansible.builtin.include_tasks", "")).endswith("single-container.yml")
)
app = workload["vars"]["k8s_app"]
assert app["host"] == "{{ actual_budget_fqdn }}"
assert app["container_port"] == "{{ app_config.app.container_port }}"
assert app["volumes"] == [
    {"name": "data", "mount_path": "/data", "storage": "{{ app_config.app.storage }}"}
]
assert app["security_context"] == {"run_as_user": 1001, "run_as_group": 1001, "fs_group": 1001}
assert app["probe"] == {"path": "/"}
assert not (ROLE / "templates/manifest.yaml.j2").exists()

backup = next(
    task for task in deploy_tasks
    if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
)
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-data"}
]
assert backup["vars"]["k8s_recovery"]["stop_replicas"] == 1
assert "database" not in backup["vars"]["k8s_recovery"]

restore_dispatch = yaml.safe_load((ROLE / "tasks/restore.yml").read_text(encoding="utf-8"))
assert any(
    str(task.get("ansible.builtin.include_tasks", "")).endswith("restore-kubernetes.yml")
    for task in restore_dispatch
)
restore = yaml.safe_load((ROLE / "tasks/restore-kubernetes.yml").read_text(encoding="utf-8"))
recovery = restore[0]["vars"]["k8s_recovery"]
assert recovery["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-data", "owner": "1001:1001"}
]
assert recovery["stop"] == ["{{ restore_target }}"]
assert recovery["after_restore_tasks"].endswith("/tasks/restore-kubernetes-password.yml")
assert "recovery-restore.yml" in restore[0]["ansible.builtin.include_tasks"]
assert not (ROLE / "templates/kubernetes/recovery-backup.yaml.j2").exists()
assert not (ROLE / "templates/kubernetes/recovery-restore.yaml.j2").exists()

reset_hook = yaml.safe_load(
    (ROLE / "tasks/restore-kubernetes-password.yml").read_text(encoding="utf-8")
)[0]
assert reset_hook["vars"]["actual_budget_reset_use_temporary_pod"] is True
assert reset_hook["vars"]["actual_budget_reset_password"] == (
    "{{ restore_target_config.app.server_password | default('') }}"
)
assert reset_hook["when"] == "restore_target_config.app.server_password | default('') | length > 0"
print("Actual Budget keeps Docker as default and uses shared Kubernetes recovery")
