#!/usr/bin/env python3
"""Guard Ollama's opt-in CPU-mode Kubernetes workload and recovery dispatch."""

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


defaults = yaml.safe_load(read("ansible/vars/app-defaults/ollama.yml"))["ollama_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["app"]["gpu_device"] == ""
assert defaults["app"]["storage"] > 0
assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"

# Deploy is the shared deploy.yml; only Docker without a stack takes Ollama's own VM, and a
# GPU is refused anywhere else.
own_vm = yaml.safe_load(read("ansible/roles/ollama/tasks/deploy-provision.yml"))
assert all("app_config.hosting.method == 'docker'" in task["when"] for task in own_vm)
prepare = {t["name"]: t for t in yaml.safe_load(read("ansible/roles/ollama/tasks/deploy-prepare.yml"))}
assert "app_config.hosting.method == 'docker'" in prepare["Ollama | Refuse a GPU off its own Docker VM"][
    "ansible.builtin.assert"]["that"]

context = {
    "instance": "ollama",
    "ollama_fqdn": "ollama.example.test",
    "app_config": defaults,
}
documents = list(yaml.safe_load_all(render(
    read("ansible/roles/ollama/templates/manifest.yaml.j2"), context
)))
objects = {document["kind"]: document for document in documents}
assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} == objects.keys()

pvc = objects["PersistentVolumeClaim"]
assert pvc["metadata"]["name"] == "ollama-storage"
assert "storageClassName" not in pvc["spec"]
assert pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"

deployment = objects["Deployment"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
pod = deployment["spec"]["template"]["spec"]
(container,) = pod["containers"]
assert container["image"] == defaults["app"]["image"]
assert container["volumeMounts"] == [{"name": "data", "mountPath": "/root/.ollama"}]
assert "resources" in container and "limits" not in container["resources"]
assert "nvidia.com/gpu" not in str(deployment), "CPU mode only"
assert container["startupProbe"]["httpGet"] == {"path": "/api/tags", "port": "web"}
assert objects["Ingress"]["spec"]["rules"][0]["host"] == "ollama.example.test"

role_tasks = yaml.safe_load(read("ansible/roles/ollama/tasks/kubernetes.yml"))
backup = next(t for t in role_tasks
              if "recovery-backup.yml" in t.get("ansible.builtin.include_tasks", ""))
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-storage"}]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

dispatch = yaml.safe_load(read("ansible/roles/ollama/tasks/restore.yml"))
for target in ("restore-kubernetes.yml", "restore-guest.yml"):
    assert any(t.get("ansible.builtin.include_tasks") == target for t in dispatch)
restore = yaml.safe_load(read("ansible/roles/ollama/tasks/restore-kubernetes.yml"))
shared = next(t for t in restore
              if "recovery-restore.yml" in t.get("ansible.builtin.include_tasks", ""))
assert shared["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-storage", "owner": "0:0"}]
assert shared["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

print("Ollama keeps Docker as default and renders its CPU-mode Kubernetes workload and recovery dispatch")
