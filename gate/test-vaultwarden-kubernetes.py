#!/usr/bin/env python3
"""Guard Vaultwarden's opt-in Kubernetes workload and shared recovery path.

Checks: the native default is unchanged, the manifest uses the default StorageClass and
the admin-token Secret, backup and restore go through the shared Kubernetes operations.
"""

import hashlib
from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/vaultwarden"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def tasks(name):
    return yaml.safe_load((ROLE / "tasks" / name).read_text(encoding="utf-8"))


defaults = yaml.safe_load(read("ansible/vars/app-defaults/vaultwarden.yml"))[
    "vaultwarden_defaults"
]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["hosting"]["impossible"] == {}
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]

playbook = yaml.safe_load(read("ansible/playbooks/apps/vaultwarden.yml"))
provision = playbook[0]["tasks"]
kube_when = "app_config.hosting.method | default('native') == 'kubernetes'"
assert any(
    task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and task.get("when") == kube_when
    for task in provision
)
assert any(
    "provision-native.yml" in str(task.get("ansible.builtin.include_tasks", ""))
    and task.get("when") == "app_config.hosting.method | default('native') == 'native'"
    for task in provision
)
deploy = playbook[1]["tasks"]
assert any(
    task.get("ansible.builtin.include_role", {}).get("name") == "vaultwarden"
    and "['native', 'docker']" in task.get("when", "")
    for task in deploy
)
text = read("ansible/playbooks/apps/vaultwarden.yml")
assert "../../tasks/kubernetes/open-app-access.yml" in text
assert "homelabinfra_infra.kubernetes.ingress_vip" in text

context = {
    "instance": "vaultwarden-test",
    "vaultwarden_fqdn": "vaultwarden-test.example.test",
    "_admin_token_plain": "token",
    "homelabinfra_config": {"timezone": "UTC"},
    "app_config": {
        "app": {
            "image": "vaultwarden/server:1.37.1",
            "port": 80,
            "storage": 5,
            "signups_allowed": False,
        }
    },
}
environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
environment.filters["hash"] = lambda value, algorithm: hashlib.new(
    algorithm, str(value).encode()
).hexdigest()
template = environment.from_string(
    (ROLE / "templates/manifest.yaml.j2").read_text(encoding="utf-8")
)


def render(**overrides):
    rendered = template.render(**{**context, **overrides})
    return {item["kind"]: item for item in yaml.safe_load_all(rendered) if item}


def pod_annotations(objects):
    return objects["Deployment"]["spec"]["template"]["metadata"]["annotations"]


objects = render()
assert pod_annotations(objects) == pod_annotations(render())
assert pod_annotations(objects) != pod_annotations(render(_admin_token_plain="rotated"))
assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} <= set(objects)
pvc = objects["PersistentVolumeClaim"]["spec"]
assert "storageClassName" not in pvc
assert pvc["resources"]["requests"]["storage"] == "5Gi"
deployment = objects["Deployment"]
container = deployment["spec"]["template"]["spec"]["containers"][0]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
assert container["volumeMounts"][0]["mountPath"] == "/data"
env = {item["name"]: item for item in container["env"]}
assert env["DOMAIN"]["value"] == "https://vaultwarden-test.example.test"
assert env["SIGNUPS_ALLOWED"]["value"] == "false"
assert env["ADMIN_TOKEN"]["valueFrom"]["secretKeyRef"] == {
    "name": "vaultwarden-test", "key": "admin-token",
}
assert container["readinessProbe"]["httpGet"] == {"path": "/alive", "port": 80}
assert objects["Ingress"]["spec"]["rules"][0]["host"] == "vaultwarden-test.example.test"

deploy_tasks = tasks("kubernetes.yml")
secret = next(
    t for t in deploy_tasks
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("sync-secret.yml")
)
assert secret["vars"]["k8s_secret_data"] == {"admin-token": "{{ _admin_token_plain }}"}
backup = next(
    t for t in deploy_tasks
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
)
recovery = backup["vars"]["k8s_recovery"]
assert recovery["archives"] == [{"name": "data", "claim": "{{ instance }}-data"}]
assert recovery["stop"] == ["{{ instance }}"]
assert "database" not in recovery

dispatch = tasks("restore.yml")
assert any(
    str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml"
    for t in dispatch
)
restore = tasks("restore-kubernetes.yml")[0]
assert restore["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/recovery-restore.yml")
assert restore["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-data", "owner": "0:0"}
]
assert restore["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]
assert not (ROLE / "templates/kubernetes").exists()
print("Vaultwarden keeps native as default and uses shared Kubernetes recovery")
