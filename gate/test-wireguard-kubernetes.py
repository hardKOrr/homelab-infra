#!/usr/bin/env python3
"""Guard WireGuard's opt-in Kubernetes workload and shared recovery path.

Checks: the native default is unchanged, the pod gets NET_ADMIN and the UDP Service
shares the ingress VIP, volumes use the default StorageClass, and backup and restore go
through the shared Kubernetes operations.
"""

import hashlib
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/wireguard"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def tasks(name):
    return yaml.safe_load((ROLE / "tasks" / name).read_text(encoding="utf-8"))


defaults = yaml.safe_load(read("ansible/vars/app-defaults/wireguard.yml"))[
    "wireguard_defaults"
]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["init_image"]
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]

playbook = yaml.safe_load(read("ansible/playbooks/apps/wireguard.yml"))
kube_when = "app_config.hosting.method | default('native') == 'kubernetes'"
provision = playbook[0]["tasks"]
assert any(
    t.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and t.get("when") == kube_when
    for t in provision
)
assert any(
    "provision-native.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    and t.get("when") == "app_config.hosting.method | default('native') == 'native'"
    for t in provision
)
assert any(
    str(t.get("ansible.builtin.include_tasks", "")).endswith("kubernetes/open-app-access.yml")
    for t in provision
)
assert "homelabinfra_infra.kubernetes.ingress_vip" in read("ansible/playbooks/apps/wireguard.yml")

traefik = read("ansible/roles/k3s_cluster/templates/traefik-config.yaml.j2")
assert "metallb.io/allow-shared-ip" in traefik

context = {
    "instance": "wireguard-test",
    "_wireguard_conf_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {
        "app": {
            "image": "linuxserver/wireguard:1.0.20260223",
            "init_image": "busybox:1.37.0",
            "interface": "wg0",
            "port": 51820,
            "storage": 1,
        }
    },
}
environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
template = environment.from_string(
    (ROLE / "templates/manifest.yaml.j2").read_text(encoding="utf-8")
)
objects = {
    item["kind"]: item
    for item in yaml.safe_load_all(template.render(**context))
    if item
}
assert {"PersistentVolumeClaim", "Deployment", "Service"} == set(objects)
pvc = objects["PersistentVolumeClaim"]["spec"]
assert "storageClassName" not in pvc
assert pvc["resources"]["requests"]["storage"] == "1Gi"
pod = objects["Deployment"]["spec"]["template"]["spec"]
container = pod["containers"][0]
assert container["securityContext"]["capabilities"]["add"] == ["NET_ADMIN"]
assert not container["securityContext"].get("privileged")
assert container["ports"][0]["protocol"] == "UDP"
assert pod["initContainers"][0]["securityContext"]["privileged"] is True
assert objects["Deployment"]["spec"]["strategy"]["type"] == "Recreate"
service = objects["Service"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["protocol"] == "UDP"
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip

deploy_tasks = tasks("kubernetes.yml")
secret_task = next(
    t for t in deploy_tasks
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("sync-secret.yml")
)
# Ansible templates mapping values, not keys. Build the filename inside an expression
# so sync-secret receives wg0.conf rather than a literal Jinja expression.
secret_expression = secret_task["vars"]["k8s_secret_data"].replace(
    "lookup('ansible.builtin.template', 'wg.conf.j2')", "'fixture-config'"
)
secret_data = Templar(
    loader=DataLoader(), variables={"app_config": {"app": {"interface": "wg0"}}}
).template(secret_expression)
assert secret_data == {"wg0.conf": "fixture-config"}
backup = next(
    t for t in deploy_tasks
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
)
recovery = backup["vars"]["k8s_recovery"]
assert recovery["archives"] == [{"name": "data", "claim": "{{ instance }}-config"}]
assert recovery["stop"] == ["{{ instance }}"]
assert "database" not in recovery
assert any(t.get("community.general.modprobe", {}).get("name") == "wireguard" for t in deploy_tasks)

names = [t["name"] for t in deploy_tasks]
checksum_task = next(t for t in deploy_tasks if t["name"].endswith("Fingerprint the tunnel configuration"))
render_task = next(t for t in deploy_tasks if t["name"].endswith("Render the workload"))
# A fact set in a task is invisible to that task's own templating: the checksum the
# manifest reads must come from an earlier task, and no task may set both.
assert "_wireguard_conf_checksum" in checksum_task["ansible.builtin.set_fact"]
assert "_wireguard_conf_checksum" not in render_task["ansible.builtin.set_fact"]
assert names.index(checksum_task["name"]) < names.index(render_task["name"])

dispatch = tasks("restore.yml")
assert any(
    str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml"
    for t in dispatch
)
restore = tasks("restore-kubernetes.yml")[0]
assert restore["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/recovery-restore.yml")
assert restore["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-config", "owner": "0:0"}
]
assert not (ROLE / "templates/kubernetes").exists()
print("WireGuard keeps native as default and uses shared Kubernetes recovery")
