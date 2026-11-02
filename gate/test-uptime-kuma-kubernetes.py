#!/usr/bin/env python3
"""Guard Uptime Kuma's opt-in Kubernetes workload and cross-method recovery.

Checks: opt-in Kubernetes workload, route and firewall selection, shared setup tasks, and
Docker/Kubernetes archive compatibility.
"""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(source)


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/uptime-kuma.yml"))["uptime_kuma_defaults"]
assert defaults["hosting"] == {
    "method": "docker", "supported": ["docker-vm", "kubernetes"], "impossible": {}
}
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"
assert {rule["port"] for rule in defaults["firewall"]["egress"]} == {80, 443}

playbook = yaml.safe_load(read("ansible/playbooks/apps/uptime-kuma.yml"))
provision = playbook[0]
stack = task_named(provision["tasks"], "Find or create stack host")
assert stack["when"] == "app_config.hosting.method == 'docker'"

docker = playbook[1]
docker_role = task_named(docker["tasks"], "Deploy Uptime Kuma")
assert docker_role["when"] == "hostvars['localhost'].app_config.hosting.method == 'docker'"
assert "configure.yml" in read("ansible/roles/uptime-kuma/tasks/main.yml")

kubernetes = playbook[2]
k8s_deploy = task_named(kubernetes["tasks"], "Deploy Uptime Kuma into its Kubernetes namespace")
assert k8s_deploy["when"] == "app_config.hosting.method == 'kubernetes'"
assert k8s_deploy["ansible.builtin.include_role"]["tasks_from"] == "kubernetes"

wire = playbook[3]
wire_vars = wire["vars"]
assert "homelabinfra_infra.kubernetes.ingress_vip" in wire_vars["wiring_upstream_host"]
assert "else _kuma_deploy_host.ansible_host" in wire_vars["wiring_upstream_host"]
assert "80 if _kuma_hosting_method == 'kubernetes'" in wire_vars["wiring_upstream_port"]
assert "Configure Uptime Kuma through its published Kubernetes route" in {
    task.get("name") for task in wire["tasks"]
}
assert wire["tasks"].index(task_named(wire["tasks"], "Wire DNS")) < wire["tasks"].index(
    task_named(wire["tasks"], "Configure Uptime Kuma through its published Kubernetes route")
)
assert "record_guest_names" in str(task_named(wire["tasks"], "Record the app on its Proxmox guest"))

manifest = list(yaml.safe_load_all(render(
    read("ansible/roles/uptime-kuma/templates/manifest.yaml.j2"),
    {
        "instance": "uptime-kuma",
        "uptime_kuma_fqdn": "uptime-kuma.example.test",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": defaults,
    },
)))
objects = {doc["kind"]: doc for doc in manifest}
assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} <= objects.keys()

pvc = objects["PersistentVolumeClaim"]
assert pvc["metadata"]["name"] == "uptime-kuma-storage"
assert "storageClassName" not in pvc["spec"]
assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"

deployment = objects["Deployment"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
container = deployment["spec"]["template"]["spec"]["containers"][0]
assert container["image"] == "louislam/uptime-kuma:2"
assert container["volumeMounts"] == [{"name": "data", "mountPath": "/app/data"}]
assert container["startupProbe"]["httpGet"] == {"path": "/api/entry-page", "port": "http"}

service = objects["Service"]
assert service["spec"]["ports"] == [{"port": 3001, "targetPort": "http"}]
assert objects["Ingress"]["spec"]["rules"][0]["host"] == "uptime-kuma.example.test"

k8s_tasks = yaml.safe_load(read("ansible/roles/uptime-kuma/tasks/kubernetes.yml"))
open_access = task_named(
    k8s_tasks, "Uptime Kuma Kubernetes | Open monitor egress through the cluster node alias"
)
assert open_access["ansible.builtin.include_tasks"].endswith(
    "tasks/kubernetes/open-app-access.yml"
)
backup = task_named(k8s_tasks, "Uptime Kuma Kubernetes | Schedule the shared application recovery")
assert backup["ansible.builtin.include_tasks"].endswith(
    "tasks/kubernetes/recovery-backup.yml"
)
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-storage"}
]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

restore_dispatch = yaml.safe_load(read("ansible/roles/uptime-kuma/tasks/restore.yml"))
assert any(task.get("ansible.builtin.include_tasks") == "restore-kubernetes.yml"
           for task in restore_dispatch)
assert any(task.get("ansible.builtin.include_tasks") == "restore-docker.yml"
           for task in restore_dispatch)
restore = yaml.safe_load(read("ansible/roles/uptime-kuma/tasks/restore-kubernetes.yml"))
shared_restore = task_named(restore, "Uptime Kuma Kubernetes restore | Use the shared recovery Job")
assert shared_restore["ansible.builtin.include_tasks"].endswith(
    "tasks/kubernetes/recovery-restore.yml"
)
assert shared_restore["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-storage"}
]
assert shared_restore["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

# Docker PBS points and Kubernetes recovery Jobs both carry the whole data directory as
# data.pxar, so a method switch does not translate or omit the live monitor database.
docker_backup = yaml.safe_load(read("ansible/roles/uptime-kuma/tasks/backup.yml"))
docker_upload = task_named(
    docker_backup,
    "Uptime Kuma backup | Quiesce Uptime Kuma and archive its data directory to PBS",
)
docker_restore = yaml.safe_load(read("ansible/roles/uptime-kuma/tasks/restore-docker.yml"))
assert docker_upload["vars"]["pbs_client_archives"] == [
    {"name": "data.pxar", "path": "{{ _kuma_backup_data_path }}"}
]
assert "data.pxar" in str(docker_restore)

common = read("ansible/roles/uptime-kuma/tasks/configure.yml")
k8s_configure = read("ansible/roles/uptime-kuma/tasks/configure-kubernetes.yml")
assert "configure.yml" in k8s_configure
assert "host: \"{{ _kuma_registry_url }}\"" in common
assert "kuma_url: \"{{ _kuma_registry_url }}\"" in common

k8s_config_tasks = yaml.safe_load(k8s_configure)
route_wait = task_named(k8s_config_tasks, "Uptime Kuma Kubernetes | Wait for its published route")
assert route_wait["failed_when"] is False
assert route_wait["retries"] == 30
route_degradation = task_named(
    k8s_config_tasks, "Uptime Kuma Kubernetes | Record an unavailable published route"
)
assert route_degradation["ansible.builtin.include_tasks"].endswith(
    "tasks/report-degradation.yml"
)
assert route_degradation["vars"]["degradation_fatal"] is False
assert "_kuma_k8s_route_reachable" in route_degradation["when"]
configure_route = task_named(
    k8s_config_tasks,
    "Uptime Kuma Kubernetes | Configure the application through the shared role tasks",
)
assert configure_route["when"] == "_kuma_k8s_route_reachable | bool"
registry_facts = task_named(
    k8s_config_tasks,
    "Uptime Kuma Kubernetes | Preserve registry facts while its route is unavailable",
)
assert "kuma_url" in registry_facts["ansible.builtin.set_fact"]
assert "kuma_admin_username" in registry_facts["ansible.builtin.set_fact"]
self_monitor = task_named(wire["tasks"], "Register uptime monitor")
assert any("_kuma_k8s_route_reachable" in condition for condition in self_monitor["when"])
notify = task_named(wire["tasks"], "Notify deployment complete")
assert "published route did not answer" in notify["vars"]["notify_message"]

print("Uptime Kuma: opt-in Kubernetes deploy, shared setup and cross-method recovery passed")
