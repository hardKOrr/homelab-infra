#!/usr/bin/env python3
"""Guard Actual Budget's opt-in Kubernetes workload and shared recovery path."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/actual-budget"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(template, context):
    environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    return environment.from_string((ROLE / "templates" / template).read_text(
        encoding="utf-8"
    )).render(**context)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/actual-budget.yml"))[
    "actual_budget_defaults"
]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
assert defaults["hosting"]["impossible"] == {}
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"]["image"]

playbook = yaml.safe_load(read("ansible/playbooks/apps/actual-budget.yml"))
provision = playbook[0]["tasks"]
assert any(
    task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and task.get("when") == "app_config.hosting.method == 'kubernetes'"
    for task in provision
)
assert any(
    task.get("ansible.builtin.import_tasks")
    == "../../tasks/stack/find-or-create-host.yml"
    and task.get("when") == "app_config.hosting.method == 'docker'"
    for task in provision
)
docker_deploy = playbook[1]["tasks"]
assert any(
    task.get("ansible.builtin.include_role", {}).get("name") == "actual-budget"
    and task.get("when") == "app_config.hosting.method == 'docker'"
    for task in docker_deploy
)
assert "../../tasks/kubernetes/open-app-access.yml" in read(
    "ansible/playbooks/apps/actual-budget.yml"
)
assert "homelabinfra_infra.kubernetes.ingress_vip" in read(
    "ansible/playbooks/apps/actual-budget.yml"
)

context = {
    "instance": "actual-budget-test",
    "actual_budget_fqdn": "actual-budget-test.example.test",
    "app_config": {
        "app": {
            "image": "docker.io/actualbudget/actual-server:latest",
            "port": 5006,
            "container_port": 5006,
            "storage": 5,
        }
    },
}
workload = [
    item for item in yaml.safe_load_all(render("manifest.yaml.j2", context)) if item
]
objects = {item["kind"]: item for item in workload}
assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} <= set(objects)
assert "storageClassName" not in objects["PersistentVolumeClaim"]["spec"]
assert objects["PersistentVolumeClaim"]["spec"]["resources"]["requests"]["storage"] == "5Gi"
deployment = objects["Deployment"]
pod = deployment["spec"]["template"]["spec"]
container = pod["containers"][0]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
assert pod["automountServiceAccountToken"] is False
assert pod["securityContext"]["runAsUser"] == 1001
assert container["volumeMounts"][0]["mountPath"] == "/data"
assert container["readinessProbe"]["httpGet"]["port"] == 5006
assert objects["Service"]["spec"]["ports"][0]["targetPort"] == 5006
assert objects["Ingress"]["spec"]["rules"][0]["host"] == "actual-budget-test.example.test"

deploy_tasks = yaml.safe_load((ROLE / "tasks/kubernetes.yml").read_text(encoding="utf-8"))
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
