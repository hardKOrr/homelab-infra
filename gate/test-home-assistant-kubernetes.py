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
assert defaults["app"]["config_storage"] > 0
assert defaults["backup"]["client"]["image"] and defaults["backup"]["encryption"] == "none"

# Deploy is the shared deploy.yml; Kubernetes takes its shared steps and the role's
# kubernetes.yml, every guest form goes through the deploy-provision hook.
provision = yaml.safe_load(read(f"{ROLE}/tasks/deploy-provision.yml"))
assert provision[0]["when"] == "app_config.hosting.method in ['docker', 'native']"
# Templates resolve by basename only inside the role's context.
for name in ("manifest.yaml.j2", "secrets.yaml.j2", "configuration.yaml.j2"):
    assert (ROOT / ROLE / "templates" / name).is_file()
vm = next(t for t in yaml.safe_load(read(f"{ROLE}/tasks/deploy-prepare.yml"))
          if t["name"].endswith("Assert the explicit VM and optional USB contract"))
assert vm["when"] == "app_config.hosting.method == 'docker'"

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
seed_command = pod["initContainers"][0]["command"][-1]
assert "test -f /config/automations.yaml ||" in seed_command
assert "printf '[]\\n' > /config/automations.yaml" in seed_command
# The editor persists automations in a separate file that Home Assistant must load.
proxies = defaults["app"]["kubernetes_trusted_proxies"]
seed = render(read(f"{ROLE}/templates/configuration.yaml.j2"), {"instance": "home-assistant", "app_config": {"app": defaults["app"]}, "_ha_trusted_proxies": proxies})
nodes = dict(yaml.compose(seed).value)
seed_fields = {key.value: value for key, value in nodes.items()}
assert seed_fields["automation"].tag == "!include"
assert seed_fields["automation"].value == "automations.yaml"
http_fields = {key.value: value for key, value in seed_fields["http"].value}
assert http_fields["use_x_forwarded_for"].value == "true"
custom = {"default_config": {}, "automation": []}
def seeded(initial, trusted):
    return yaml.safe_load(render(read(f"{ROLE}/templates/configuration.yaml.j2"), {
        "instance": "home-assistant", "_ha_trusted_proxies": trusted,
        "app_config": {"app": dict(defaults["app"], initial_configuration=initial)}}))
assert seeded(custom, proxies) == custom
# An initial_configuration that forwards also trusts the current proxy; a stale address stays.
forwarding = {"default_config": {}, "http": {"use_x_forwarded_for": True, "trusted_proxies": ["192.0.2.1"]}}
assert seeded(forwarding, ["192.0.2.9"])["http"]["trusted_proxies"] == ["192.0.2.1", "192.0.2.9"]
# Without a reverse proxy the default seed has no http block.
assert "http" not in yaml.safe_load(render(read(f"{ROLE}/templates/configuration.yaml.j2"), {
    "instance": "home-assistant", "_ha_trusted_proxies": [],
    "app_config": {"app": defaults["app"]}}).replace("!include", ""))
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
    "restore-kubernetes.yml", "restore-guest.yml"}
restore = yaml.safe_load(read(f"{ROLE}/tasks/restore-kubernetes.yml"))
shared = next(t for t in restore if "recovery-restore.yml" in t.get("ansible.builtin.include_tasks", ""))
assert shared["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-config", "owner": "0:0"}]
assert shared["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]

print("Home Assistant keeps Docker as default and renders its Kubernetes workload, USB pinning and recovery")
