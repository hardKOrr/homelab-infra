#!/usr/bin/env python3
"""Guard Karakeep's opt-in Kubernetes workload and recovery dispatch."""

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


def render(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/karakeep.yml"))["karakeep_defaults"]
assert defaults["hosting"] == {
    "method": "docker", "supported": ["docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm"],
    "impossible": {}
}
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"] == {
    "image": "buildpack-deps:bookworm-curl",
    "key_url": "https://enterprise.proxmox.com/debian/proxmox-release-bookworm.gpg",
    "repository": "http://download.proxmox.com/debian/pbs-client",
    "suite": "bookworm",
    "component": "main",
}

# A shim on playbooks/apps/deploy.yml; gate/app-matrix.yml records what each method reaches.
assert yaml.safe_load(read("ansible/playbooks/apps/karakeep.yml")) == [{
    "name": "Deploy karakeep",
    "ansible.builtin.import_playbook": "deploy.yml",
    "vars": {"app": "karakeep"},
}]

context = {
    "instance": "karakeep",
    "karakeep_fqdn": "karakeep.example.test",
    "karakeep_data_uid": 1000,
    "karakeep_data_gid": 1000,
    "app_config": defaults,
}
documents = list(yaml.safe_load_all(render(
    read("ansible/roles/karakeep/templates/manifest.yaml.j2"), context
)))
objects = {document["kind"]: document for document in documents}
assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} <= objects.keys()

pvc = objects["PersistentVolumeClaim"]
assert pvc["metadata"]["name"] == "karakeep-storage"
assert "storageClassName" not in pvc["spec"]
assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"

deployment = objects["Deployment"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
pod = deployment["spec"]["template"]["spec"]
containers = {container["name"]: container for container in pod["containers"]}
assert set(containers) == {"web", "chrome", "meilisearch"}
assert containers["web"]["image"] == defaults["app"]["image"]
assert containers["chrome"]["image"] == defaults["app"]["chrome_image"]
assert containers["meilisearch"]["image"] == defaults["app"]["meilisearch_image"]
assert containers["web"]["volumeMounts"] == [{
    "name": "data", "mountPath": "/data", "subPath": "data"
}]
assert containers["meilisearch"]["volumeMounts"] == [{
    "name": "data", "mountPath": "/meili_data", "subPath": "meilisearch"
}]
assert containers["web"]["env"][-3:] == [
    {"name": "MEILI_ADDR", "value": "http://localhost:7700"},
    {"name": "BROWSER_WEB_URL", "value": "http://localhost:9222"},
    {"name": "DATA_DIR", "value": "/data"},
]
assert containers["web"]["startupProbe"]["httpGet"] == {
    "path": "/api/health", "port": "web"
}
assert containers["web"]["env"][2]["valueFrom"]["secretKeyRef"] == {
    "name": "karakeep", "key": "nextauth-secret"
}

service = objects["Service"]
assert service["spec"]["ports"] == [{"port": 3005, "targetPort": "web"}]
ingress = objects["Ingress"]
assert ingress["spec"]["rules"][0]["host"] == "karakeep.example.test"

role_tasks = yaml.safe_load(read("ansible/roles/karakeep/tasks/kubernetes.yml"))
backup = next(task for task in role_tasks
              if "recovery-backup.yml" in task.get("ansible.builtin.include_tasks", ""))
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-storage"}
]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

restore_dispatch = yaml.safe_load(read("ansible/roles/karakeep/tasks/restore.yml"))
assert any("restore-kubernetes.yml" == task.get("ansible.builtin.include_tasks")
           for task in restore_dispatch)
assert any("restore-guest.yml" == task.get("ansible.builtin.include_tasks")
           for task in restore_dispatch)
restore = yaml.safe_load(read("ansible/roles/karakeep/tasks/restore-kubernetes.yml"))
shared_restore = next(task for task in restore
                      if "recovery-restore.yml" in task.get("ansible.builtin.include_tasks", ""))
assert shared_restore["vars"]["k8s_recovery"]["archives"] == [{
    "name": "data", "claim": "{{ restore_target }}-storage", "owner": "{{ karakeep_data_uid }}:{{ karakeep_data_gid }}"
}]
assert shared_restore["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

assert "credentials.yml" in read("ansible/roles/karakeep/tasks/main.yml")
assert "tasks/stack/compose-deploy.yml" in read("ansible/roles/karakeep/tasks/docker.yml")

print("Karakeep keeps Docker as default and renders its Kubernetes workload and recovery dispatch")
