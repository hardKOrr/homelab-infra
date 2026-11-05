#!/usr/bin/env python3
"""Guard Home Assistant's Kubernetes workload, USB pinning and recovery dispatch."""

from pathlib import Path
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ROLE = "ansible/roles/home-assistant"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


defaults = yaml.safe_load(read("ansible/vars/app-defaults/home-assistant.yml"))["home_assistant_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]
assert defaults["app"]["config_storage"] > 0
assert defaults["backup"]["client"]["image"] and defaults["backup"]["encryption"] == "none"

playbook = yaml.safe_load(read("ansible/playbooks/apps/home-assistant.yml"))
provision, wire = playbook[0], playbook[-1]
branch = next(t for t in provision["tasks"] if t.get("block") and "kubernetes" in t["when"])
assert branch["when"] == "app_config.hosting.method == 'kubernetes'"
assert "tasks/kubernetes.yml" in branch["block"][0]["ansible.builtin.include_tasks"]
assert branch["block"][1]["ansible.builtin.meta"] == "end_play"
assert provision["tasks"].index(branch) == 0
vm = next(t for t in provision["pre_tasks"] if t["name"].startswith("Assert the explicit VM"))
assert vm["when"] == "app_config.hosting.method == 'docker'"
assert "homelabinfra_infra.kubernetes.ingress_vip" in str(wire["vars"]["wiring_upstream_host"])
assert "80 if" in str(wire["vars"]["wiring_upstream_port"])

base = {
    "instance": "home-assistant", "home_assistant_fqdn": "home-assistant.example.test",
    "homelabinfra_config": {"timezone": "UTC"}, "_ha_usb_node": "k3s-1",
    "_ha_seed_configuration": "default_config: {}\n",
}


def objects_for(usb):
    app = dict(defaults["app"], usb_mapping="coordinator" if usb else "")
    manifest = render(read(f"{ROLE}/templates/manifest.yaml.j2"), dict(base, app_config={"app": app}))
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(manifest)}


plain = objects_for(False)
assert {kind for kind, _ in plain} == {"ConfigMap", "PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
assert "storageClassName" not in plain[("PersistentVolumeClaim", "home-assistant-config")]["spec"]
deployment = plain[("Deployment", "home-assistant")]
pod = deployment["spec"]["template"]["spec"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
assert "nodeSelector" not in pod and "securityContext" not in pod["containers"][0]
assert all("hostPath" not in v for v in pod["volumes"])
assert plain[("Ingress", "home-assistant")]["spec"]["rules"][0]["host"] == "home-assistant.example.test"

usb = objects_for(True)[("Deployment", "home-assistant")]["spec"]["template"]["spec"]
assert usb["nodeSelector"] == {"kubernetes.io/hostname": "k3s-1"}
assert usb["containers"][0]["securityContext"]["privileged"] is True
assert next(v for v in usb["volumes"] if v["name"] == "coordinator")["hostPath"] == {
    "path": "/dev/ttyUSB0", "type": "CharDevice"}
assert {"name": "coordinator", "mountPath": "/dev/ttyUSB0"} in usb["containers"][0]["volumeMounts"]

tasks = yaml.safe_load(read(f"{ROLE}/tasks/kubernetes.yml"))
attach = next(t for t in tasks if "attach-usb-passthrough.yml" in t.get("ansible.builtin.include_tasks", ""))
assert "length > 0" in attach["when"]
backup = next(t for t in tasks if "recovery-backup.yml" in t.get("ansible.builtin.include_tasks", ""))
assert backup["vars"]["k8s_recovery"]["archives"] == [{"name": "data", "claim": "{{ instance }}-config"}]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

dispatch = yaml.safe_load(read(f"{ROLE}/tasks/restore.yml"))
assert {t.get("ansible.builtin.include_tasks") for t in dispatch} >= {
    "restore-kubernetes.yml", "restore-docker.yml"}
restore = yaml.safe_load(read(f"{ROLE}/tasks/restore-kubernetes.yml"))
shared = next(t for t in restore if "recovery-restore.yml" in t.get("ansible.builtin.include_tasks", ""))
assert shared["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-config", "owner": "0:0"}]
assert shared["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

print("Home Assistant keeps Docker as default and renders its Kubernetes workload, USB pinning and recovery")
