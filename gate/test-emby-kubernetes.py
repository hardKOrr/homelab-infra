#!/usr/bin/env python3
"""Guard Emby's Docker default and its Kubernetes workload, iGPU pinning and recovery dispatch."""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ROLE = "ansible/roles/emby"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def tasks(name):
    return yaml.safe_load(read(f"{ROLE}/tasks/{name}"))


def include(task_list, target):
    return next(t for t in task_list if target in str(t.get("ansible.builtin.include_tasks", "")))


def render(template, variables):
    return Templar(loader=DataLoader(), variables=variables).template(template)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/emby.yml"))["emby_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm"]
assert defaults["app"]["storage"] > 0

# Docker keeps the stack-host path and shares the API configuration with Kubernetes.
main = tasks("main.yml")
for shared in ("resolve.yml", "credentials.yml", "configure.yml", "../../../tasks/stack/compose-deploy.yml"):
    include(main, shared)
kubernetes = tasks("kubernetes.yml")
for shared in ("resolve.yml", "credentials.yml", "configure.yml", "kubernetes-gpu.yml"):
    include(kubernetes, shared)
assert include(kubernetes, "kubernetes-gpu.yml")["when"] == "_emby_gpu_declared | bool"

# Media: the same NFS shares at the same container paths as Docker's bind mounts, read-only,
# through the shared seam and nothing role-local.
shares = include(kubernetes, "prepare-media-volumes.yml")["vars"]["k8s_media_shares"]
assert "'mount_path': share.path" in shares and "'read_only': true" in shares
prepare = tasks("deploy-prepare.yml")
resolve = include(prepare, "resolve-media-nfs.yml")
assert resolve["when"] == "app_config.hosting.method == 'kubernetes'"
assert next(t for t in prepare if "deploy_host_mounts" in str(t.get("ansible.builtin.set_fact")))[
    "when"] == "app_config.hosting.method in ['docker', 'native']"

# The config volume is the application-consistent recovery unit.
backup = include(kubernetes, "recovery-backup.yml")["vars"]["k8s_recovery"]
assert backup["archives"][0]["claim"] == "{{ instance }}-config" and backup["stop"] == ["{{ instance }}"]
restore = tasks("restore-kubernetes.yml")
# Missing source credentials must fail before the recovery Job empties the PVC.
assert "restore-contract.yml" in str(restore[0]["ansible.builtin.include_tasks"])
contract = tasks("restore-contract.yml")[0]["ansible.builtin.assert"]["that"]
assert any("restore_source_emby_password" in c for c in contract)
assert any("restore_source_emby_user" in c for c in contract)
assert include(tasks("restore-guest.yml"), "restore-contract.yml")
restore = restore[1:]
assert "tasks/kubernetes/recovery-restore.yml" in str(restore[0]["ansible.builtin.include_tasks"])
assert restore[0]["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"
verified = include(restore, "restore-verified.yml")
assert verified["when"] == "_application_restore_complete | default(false) | bool"
assert "Host" in verified["vars"]["_emby_restore_headers"]
# The credentials go to the target's item, not the source's (`instance` is the source here).
assert verified["vars"]["_emby_restore_instance"] == "{{ restore_target }}"
hook = tasks("restore-verified.yml")
record = next(t for t in hook if "upsert-item.yml" in str(t.get("ansible.builtin.include_tasks")))
assert record["vars"]["vault_item_name"] == "homelab-infra/media/{{ _emby_restore_instance }}"
assert "_emby_restore_instance | default(instance)" in str(hook[0])
assert "{{ instance }}" not in read(f"{ROLE}/tasks/restore-verified.yml").replace(
    "default(instance)", "")
dispatch = tasks("restore.yml")
assert include(dispatch, "restore-kubernetes.yml") and include(dispatch, "restore-guest.yml")

# Emby supplies k8s_app to the shared single-container workload; render both exactly as the
# play does, with and without an iGPU.
deploy = next(t for t in kubernetes if t.get("name") == "Emby Kubernetes | Deploy the workload")
assert deploy["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/single-container.yml")
shared = read("ansible/templates/kubernetes/single-container.yaml.j2")


def workload(gpu):
    app = dict(defaults["app"], gpu_device="intel-igpu-2" if gpu else "")
    variables = {
        "instance": "emby", "_deploy_fqdn": "emby.example.test",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": dict(defaults, app=app),
        "k8s_media_volumes": [{"name": "media-0", "nfs": {
            "server": "192.0.2.50", "path": "/export/media", "readOnly": True}}],
        "k8s_media_volume_mounts": [{"name": "media-0", "mountPath": "/mnt/media"}],
        "_emby_gpu_declared": gpu, "_emby_gpu_nodes": ["k3s-4"] if gpu else [],
        **deploy["vars"],
    }
    variables["k8s_app"] = render(deploy["vars"]["k8s_app"], variables)
    docs = yaml.safe_load_all(render(shared, variables))
    return {(d["kind"], d["metadata"]["name"]): d for d in docs}


cpu = workload(False)
assert {kind for kind, _ in cpu} == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
pod = cpu[("Deployment", "emby")]["spec"]["template"]["spec"]
assert "nodeSelector" not in pod and "hostPath" not in str(pod["volumes"])
assert "securityContext" not in pod["containers"][0]
claim = cpu[("PersistentVolumeClaim", "emby-config")]["spec"]
assert "storageClassName" not in claim  # the cluster's default StorageClass
nfs = next(v for v in pod["volumes"] if v["name"] == "media-0")["nfs"]
assert nfs["readOnly"] is True
assert {"name": "media-0", "mountPath": "/mnt/media"} in pod["containers"][0]["volumeMounts"]
assert cpu[("Ingress", "emby")]["spec"]["rules"][0]["host"] == "emby.example.test"
assert cpu[("Service", "emby")]["spec"]["ports"][0] == {"port": 8097, "targetPort": 8096}
assert pod["containers"][0]["readinessProbe"]["httpGet"]["path"] == "/emby/System/Info/Public"

gpu = workload(True)
pod = gpu[("Deployment", "emby")]["spec"]["template"]["spec"]
assert pod["nodeSelector"] == {"kubernetes.io/hostname": "k3s-4"}
assert pod["containers"][0]["securityContext"] == {"privileged": True}
assert {"name": "dri", "mountPath": "/dev/dri"} in pod["containers"][0]["volumeMounts"]
assert next(v for v in pod["volumes"] if v["name"] == "dri")["hostPath"]["path"] == "/dev/dri"
assert {"name": "ATTACHED_DEVICES_PERMS", "value": "/dev/dri -type c"} in pod["containers"][0]["env"]

# A cluster node passes its declared iGPU through; the role installs a DRM kernel on it.
node = yaml.safe_load(read("ansible/tasks/kubernetes/provision-node.yml"))
attach = include(node, "attach-pci-passthrough.yml")
assert attach["when"] == "k8s_node.gpu_device | default('', true) | length > 0"
assert "k8s_node_spec.gpu_device" in read("ansible/roles/k3s_cluster/tasks/main.yml")
assert "linux-image-amd64" in read("ansible/roles/k3s_cluster/tasks/gpu.yml")

print("Emby keeps Docker as default and renders its Kubernetes workload, iGPU pinning and recovery dispatch")
