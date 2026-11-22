#!/usr/bin/env python3
"""Guard Frigate's CPU-only Kubernetes workload and recovery dispatch."""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ROLE = "ansible/roles/frigate"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/frigate.yml"))["frigate_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["app"]["config_storage"] > 0 and defaults["app"]["media_storage"] > 0
assert defaults["backup"]["client"] == {
    "image": "buildpack-deps:bookworm-curl",
    "key_url": "https://enterprise.proxmox.com/debian/proxmox-release-bookworm.gpg",
    "repository": "http://download.proxmox.com/debian/pbs-client",
    "suite": "bookworm",
    "component": "main",
}
assert defaults["backup"]["timeout_seconds"] > 0 and defaults["backup"]["scratch_size"] > 0
assert defaults["backup"]["encryption"] == "none"

# Deploy is the shared deploy.yml; only Docker Frigate provisions its own host and devices.
provision = yaml.safe_load(read(f"{ROLE}/tasks/deploy-provision.yml"))
assert provision[0]["when"] == "app_config.hosting.method == 'docker'"
assert any("provision-docker.yml" in str(t.get("ansible.builtin.include_tasks"))
           for t in provision[0]["block"])

cpu = dict(defaults["app"], detector="cpu", gpu_device="", igpu_render_node="",
           coral_usb_mapping="", hwaccel_args="", cameras={})
context = {
    "instance": "frigate", "frigate_fqdn": "frigate.example.test",
    "ansible_managed": "managed", "_frigate_mqtt_password": "",
    "app_config": dict(defaults, app=cpu),
}
context["_frigate_seed_config"] = render(read(f"{ROLE}/templates/config.yml.j2"), context)
manifest = render(read(f"{ROLE}/templates/manifest.yaml.j2"), context)
objects = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(manifest)}
assert {kind for kind, _ in objects} == {
    "ConfigMap", "PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
seed = objects[("ConfigMap", "frigate-seed")]["data"]["config.yml"]
assert yaml.safe_load(seed)["detectors"] == {"cpu1": {"type": "cpu"}}
for claim in ("frigate-config", "frigate-media"):
    pvc = objects[("PersistentVolumeClaim", claim)]
    assert "storageClassName" not in pvc["spec"]
pod = objects[("Deployment", "frigate")]["spec"]["template"]["spec"]
assert objects[("Deployment", "frigate")]["spec"]["strategy"]["type"] == "Recreate"
container = pod["containers"][0]
assert container["image"] == defaults["app"]["image"]
assert "resources" in container and not any("devices" in str(v) for v in pod.values()
                                           if isinstance(v, dict) and "hostPath" in v)
assert all("hostPath" not in volume for volume in pod["volumes"])
assert next(v for v in pod["volumes"] if v["name"] == "shm")["emptyDir"]["sizeLimit"] == "256Mi"
assert "test -f /config/config.yml" in pod["initContainers"][0]["command"][-1]
assert objects[("Ingress", "frigate")]["spec"]["rules"][0]["host"] == "frigate.example.test"

tasks = yaml.safe_load(read(f"{ROLE}/tasks/kubernetes.yml"))
# deploy.yml opens the declared cluster access before the role runs; the role keeps no copy.
assert not any("open-app-access.yml" in str(t.get("ansible.builtin.include_tasks")) for t in tasks)
backup = next(t for t in tasks if "recovery-backup.yml" in t.get("ansible.builtin.include_tasks", ""))
assert backup["vars"]["k8s_recovery"]["archives"] == [{"name": "data", "claim": "{{ instance }}-config"}]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

dispatch = yaml.safe_load(read(f"{ROLE}/tasks/restore.yml"))
assert {t.get("ansible.builtin.include_tasks") for t in dispatch} >= {
    "restore-kubernetes.yml", "restore-guest.yml"}
restore = yaml.safe_load(read(f"{ROLE}/tasks/restore-kubernetes.yml"))
shared = next(t for t in restore if "recovery-restore.yml" in t.get("ansible.builtin.include_tasks", ""))
assert shared["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-config", "owner": "0:0"}]
assert shared["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

print("Frigate keeps Docker as default and renders its CPU-only Kubernetes workload and recovery dispatch")
