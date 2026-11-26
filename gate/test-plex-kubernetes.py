#!/usr/bin/env python3
"""Guard Plex's Docker default and its Kubernetes workload, iGPU pinning, claim and recovery."""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ROLE = "ansible/roles/plex"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def tasks(name):
    return yaml.safe_load(read(f"{ROLE}/tasks/{name}"))


def include(task_list, target):
    return next(t for t in task_list if target in str(t.get("ansible.builtin.include_tasks", "")))


def render(template, variables):
    return Templar(loader=DataLoader(), variables=variables).template(template)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/plex.yml"))["plex_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]
assert defaults["app"]["storage"] > 0

# Docker keeps the stack-host path; both paths share the preferences reader and transcoder.
main = tasks("main.yml")
assert include(main, "../../../tasks/stack/compose-deploy.yml")
assert include(main, "configure-transcoder.yml")
kubernetes = tasks("kubernetes.yml")
for shared in ("configure-transcoder.yml", "kubernetes-gpu.yml", "read-preferences.yml",
               "../../../tasks/kubernetes/recovery-backup.yml", "../../../tasks/kubernetes/sync-secret.yml"):
    include(kubernetes, shared)
assert include(kubernetes, "kubernetes-gpu.yml")["when"] == "_plex_gpu_declared | bool"

# Media: the same NFS shares at the same container paths as Docker's bind mounts, read-only,
# through the shared seam and nothing role-local.
shares = include(kubernetes, "prepare-media-volumes.yml")["vars"]["k8s_media_shares"]
assert "'mount_path': share.path" in shares and "'read_only': true" in shares
prepare = tasks("deploy-prepare.yml")
resolve = include(prepare, "resolve-media-nfs.yml")
assert resolve["when"] == "app_config.hosting.method == 'kubernetes'"
# deploy.yml attaches deploy_host_mounts to a stack host only on Docker and native.
assert "app_config.hosting.method in ['docker', 'native']" in read("ansible/playbooks/apps/deploy.yml")

# The claim token is spent by the first start: the workload is applied with it, then without.
claim = include(kubernetes, "sync-secret.yml")
assert claim["when"] == "not _plex_claimed"
first = include(kubernetes, "kubernetes-workload.yml")
assert first["vars"]["_plex_k8s_claiming"] == "{{ not _plex_claimed }}"
rolled = next(t for t in kubernetes if t.get("block"))["block"]
assert include(rolled, "kubernetes-workload.yml")["vars"]["_plex_k8s_claiming"] is False

# The config volume is the application-consistent recovery unit.
backup = include(kubernetes, "recovery-backup.yml")["vars"]["k8s_recovery"]
assert backup["archives"][0]["claim"] == "{{ instance }}-config" and backup["stop"] == ["{{ instance }}"]
restore = tasks("restore-kubernetes.yml")
job = include(restore, "tasks/kubernetes/recovery-restore.yml")
assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"
verified = next(t for t in restore if t.get("block"))
assert verified["when"] == "_application_restore_complete | default(false) | bool"
publish = include(verified["block"], "upsert-item.yml")
assert publish["vars"]["vault_item_name"] == "homelab-infra/media/{{ restore_target }}"
dispatch = tasks("restore.yml")
assert include(dispatch, "restore-kubernetes.yml") and include(dispatch, "restore-guest.yml")

# Plex supplies k8s_app to the shared single-container workload; render both exactly as the
# play does, with and without an iGPU and the claim.
workload_tasks = tasks("kubernetes-workload.yml")
deploy = workload_tasks[0]
assert deploy["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/single-container.yml")
shared = read("ansible/templates/kubernetes/single-container.yaml.j2")
configmap = read(f"{ROLE}/templates/device-groups-configmap.yaml.j2")


def workload(gpu, claiming=False, token="claim-one"):
    app = dict(defaults["app"], gpu_device="intel-igpu-2" if gpu else "")
    variables = {
        "instance": "plex", "_deploy_fqdn": "plex.example.test",
        "homelabinfra_config": {"timezone": "UTC"},
        "app_config": dict(defaults, app=app),
        "_plex_advertise_url": "https://plex.example.test:443",
        "k8s_media_volumes": [{"name": "media-0", "nfs": {
            "server": "192.0.2.50", "path": "/export/media", "readOnly": True}}],
        "k8s_media_volume_mounts": [{"name": "media-0", "mountPath": "/mnt/media", "readOnly": True}],
        "_plex_gpu_declared": gpu, "_plex_gpu_nodes": ["k3s-4"] if gpu else [],
        "_plex_k8s_claiming": claiming, "_plex_claim_token": token,
        **deploy["vars"],
    }
    variables["k8s_app"] = render(deploy["vars"]["k8s_app"], variables)
    docs = yaml.safe_load_all(render(shared, variables))
    result = {(d["kind"], d["metadata"]["name"]): d for d in docs}
    if gpu:
        assert "role_path ~ '/templates/device-groups-configmap.yaml.j2'" in deploy["vars"]["k8s_app_extra_manifest"]
        result.update({(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(render(configmap, variables))})
    return result


cpu = workload(False)
assert {kind for kind, _ in cpu} == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
pod = cpu[("Deployment", "plex")]["spec"]["template"]["spec"]
container = pod["containers"][0]
assert "nodeSelector" not in pod and "hostPath" not in str(pod["volumes"])
assert "securityContext" not in container
assert "storageClassName" not in cpu[("PersistentVolumeClaim", "plex-config")]["spec"]
assert next(v for v in pod["volumes"] if v["name"] == "media-0")["nfs"]["readOnly"] is True
assert {"name": "media-0", "mountPath": "/mnt/media", "readOnly": True} in container["volumeMounts"]
assert {"name": "config", "mountPath": "/config"} in container["volumeMounts"]
assert cpu[("Ingress", "plex")]["spec"]["rules"][0]["host"] == "plex.example.test"
assert cpu[("Service", "plex")]["spec"]["ports"][0] == {"port": 32400, "targetPort": 32400}
assert container["readinessProbe"]["httpGet"]["path"] == "/identity"
env = {e["name"]: e for e in container["env"]}
assert env["PLEX_UID"]["value"] == "1313" and "PLEX_CLAIM" not in env
claiming = workload(False, claiming=True)[("Deployment", "plex")]["spec"]["template"]["spec"]["containers"][0]
assert {"name": "PLEX_CLAIM", "valueFrom": {"secretKeyRef": {"name": "plex", "key": "claim_token"}}} in claiming["env"]

def annotations(token, claiming=True):
    template = workload(False, claiming, token)[("Deployment", "plex")]["spec"]["template"]
    return template["metadata"].get("annotations", {})


# Retrying an unclaimed server with a replacement token rolls the pod, without exposing it.
first, second = annotations("claim-one"), annotations("claim-two")
assert first and first != second and "claim-one" not in str(first)
assert annotations("claim-one") == first and annotations("claim-one", False) == {}

gpu = workload(True)
pod = gpu[("Deployment", "plex")]["spec"]["template"]["spec"]
container = pod["containers"][0]
assert pod["nodeSelector"] == {"kubernetes.io/hostname": "k3s-4"}
assert container["securityContext"] == {"privileged": True}
assert {"name": "dri", "mountPath": "/dev/dri"} in container["volumeMounts"]
assert next(v for v in pod["volumes"] if v["name"] == "dri")["hostPath"]["path"] == "/dev/dri"
groups = next(m for m in container["volumeMounts"] if m["name"] == "device-groups")
assert groups["mountPath"] == "/etc/cont-init.d/46-plex-device-groups"
assert next(v for v in pod["volumes"] if v["name"] == "device-groups")["configMap"] == {
    "name": "plex-device-groups", "defaultMode": 0o755}
assert "usermod -a -G" in gpu[("ConfigMap", "plex-device-groups")]["data"]["device-groups.sh"]

# The probe proves the render node, the service user's access and VA-API's H.264 profiles.
probe = read(f"{ROLE}/tasks/kubernetes-gpu.yml")
assert "renderD" in probe and "su -s /bin/sh plex" in probe and "VAProfileH264" in probe

# A cluster node passes its declared iGPU through; the role installs a DRM kernel on it.
node = yaml.safe_load(read("ansible/tasks/kubernetes/provision-node.yml"))
attach = include(node, "attach-pci-passthrough.yml")
assert attach["when"] == "k8s_node.gpu_device | default('', true) | length > 0"

print("Plex keeps Docker as default and renders its Kubernetes workload, claim, iGPU pinning and recovery dispatch")
