#!/usr/bin/env python3
"""Check Ntfy's opt-in Kubernetes workload and shared recovery adapters."""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/ntfy"
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/ntfy.yml"))["ntfy_defaults"]
assert defaults["hosting"] == {
    "method": "native", "supported": ["native-lxc", "native-vm", "kubernetes"], "impossible": {}
}
assert defaults["recovery"]["methods"] == ["native"]
assert defaults["backup"]["schedule"]
assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"

playbook = yaml.safe_load(read("ansible/playbooks/apps/ntfy.yml"))
provision = playbook[0]
validate = next(task for task in provision["pre_tasks"]
                if task.get("name") == "Validate supported hosting method")
assert validate["ansible.builtin.include_tasks"] == "../../tasks/hosting/validate-app.yml"
kubernetes = next(task for task in provision["tasks"]
                  if task.get("name") == "Deploy Ntfy on Kubernetes")
assert kubernetes["ansible.builtin.include_role"]["tasks_from"] == "kubernetes"
assert kubernetes["when"] == "app_config.hosting.method == 'kubernetes'"
native = next(task for task in provision["tasks"]
              if task.get("name") == "Provision or reuse the native guest")
assert native["ansible.builtin.include_role"] == {"name": "_template-native", "tasks_from": "provision"}
assert native["when"] == "app_config.hosting.method == 'native'"
shared = read("ansible/roles/_template-native/tasks/provision.yml")
assert 'resolve_guest_type: "{{ _native_guest_guest_type }}"' in shared
assert "resolve-native-type.yml" in shared
record_facts = next(task for task in playbook[2]["pre_tasks"]
                    if task.get("ansible.builtin.import_tasks", "").endswith(
                        "tasks/bootstrap/write-generated-facts.yml"))
assert record_facts["vars"]["generated_facts_service"] == "notifications"
assert set(record_facts["vars"]["generated_facts_data"]) == {
    "provider", "instance", "host", "topic"
}
assert "tasks/kubernetes/open-app-access.yml" in read("ansible/roles/ntfy/tasks/kubernetes.yml")

role_tasks = yaml.safe_load(read("ansible/roles/ntfy/tasks/kubernetes.yml"))
estate = role_tasks[0]
assert estate["ansible.builtin.include_tasks"] == "../../../tasks/resolve-estate.yml"
assert estate["vars"]["resolve_estate_name"] == "{{ app_config.routing.estate | default('') }}"
apply = next(task for task in role_tasks
             if task.get("ansible.builtin.include_tasks", "").endswith("tasks/kubernetes/apply-manifest.yml"))
assert apply["vars"]["k8s_wait_for"] == ["deployment/{{ instance }}"]
backup = next(task for task in role_tasks
              if task.get("ansible.builtin.include_tasks", "").endswith("tasks/kubernetes/recovery-backup.yml"))
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-storage"}
]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

restore_tasks = yaml.safe_load(read("ansible/roles/ntfy/tasks/restore-kubernetes.yml"))
restore = next(task for task in restore_tasks
               if "recovery-restore.yml" in task.get("ansible.builtin.include_tasks", ""))
assert restore["vars"]["k8s_recovery"]["archives"] == [{
    "name": "data", "claim": "{{ restore_target }}-storage", "owner": "0:0"
}]
assert restore["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]
assert any(task.get("ansible.builtin.include_tasks") == "credentials.yml"
           for task in restore_tasks)
restore_facts = next(task for task in restore_tasks
                     if task.get("ansible.builtin.include_tasks", "").endswith(
                         "tasks/bootstrap/write-generated-facts.yml"))
assert restore_facts["vars"]["generated_facts_service"] == "notifications"
assert set(restore_facts["vars"]["generated_facts_data"]) == {
    "provider", "instance", "host", "topic"
}
dispatch = yaml.safe_load(read("ansible/roles/ntfy/tasks/restore.yml"))
assert any(task.get("ansible.builtin.include_tasks") == "restore-kubernetes.yml"
           for task in dispatch)
assert any(task.get("ansible.builtin.include_tasks") == "restore-native.yml"
           for task in dispatch)

context = {
    "role_path": str(ROLE),
    "instance": "ntfy",
    "ntfy_fqdn": "ntfy.example.test",
    "_ntfy_domain": "example.test",
    "ansible_managed": "managed by gate",
    "ntfy_auth_file": "/var/lib/ntfy/user.db",
    "ntfy_cache_file": "/var/cache/ntfy/cache.db",
    "app_config": defaults,
}
documents = list(yaml.safe_load_all(render(
    read("ansible/roles/ntfy/templates/manifest.yaml.j2"), context
)))
objects = {document["kind"]: document for document in documents}
assert set(objects) == {"ConfigMap", "PersistentVolumeClaim", "Deployment", "Service", "Ingress"}

claim = objects["PersistentVolumeClaim"]
assert claim["metadata"]["name"] == "ntfy-storage"
assert "storageClassName" not in claim["spec"]
assert claim["spec"]["resources"]["requests"]["storage"] == "4Gi"

deployment = objects["Deployment"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
pod = deployment["spec"]["template"]["spec"]
container = pod["containers"][0]
assert container["image"] == "binwiederhier/ntfy:latest"
assert container["args"] == ["serve"]
assert container["env"][0]["valueFrom"]["secretKeyRef"] == {
    "name": "ntfy", "key": "publish-password"
}
mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
assert mounts["/var/lib/ntfy"]["subPath"] == "data"
assert mounts["/var/cache/ntfy"]["subPath"] == "cache"
config = objects["ConfigMap"]["data"]["server.yml"]
assert "base-url: \"https://ntfy.example.test\"" in config
assert "auth-file: \"/var/lib/ntfy/user.db\"" in config
assert "cache-file: \"/var/cache/ntfy/cache.db\"" in config
assert "auth-default-access: \"deny-all\"" in config

service = objects["Service"]
assert service["spec"]["ports"] == [{"port": 80, "targetPort": "http"}]
assert objects["Ingress"]["spec"]["rules"][0]["host"] == "ntfy.example.test"

print("Ntfy keeps native as default and renders its Kubernetes workload and shared recovery inputs")
