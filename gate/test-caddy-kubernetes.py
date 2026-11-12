#!/usr/bin/env python3
"""Caddy's Kubernetes method renders its Service and workload, and routes backup, restore
and the playbook through the shared Kubernetes operations."""
import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
init_plugin_loader()
ROLE = "ansible/roles/caddy"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(name, **extra):
    variables = {"instance": "caddy", "app_config": defaults, "_caddy_admin_origin": "192.0.2.50:2019",
                 "_caddy_modules_wanted": [], "_caddy_image_tag": "abc123", **extra}
    text = read(f"{ROLE}/templates/{name}")
    return list(yaml.safe_load_all(Templar(loader=DataLoader(), variables=variables).template(text)))


defaults = yaml.safe_load(read("ansible/vars/app-defaults/caddy.yml"))["caddy_defaults"]
tls = yaml.safe_load(read(f"{ROLE}/tasks/main/tls.yml"))
dns_facts = next(t for t in tls if "ansible.builtin.setup" in t)
assert dns_facts["when"] == "caddy_kubernetes | bool"
assert "dns" in dns_facts["ansible.builtin.setup"]["gather_subset"]
assert tls.index(dns_facts) < next(i for i, t in enumerate(tls) if t.get("name") == "Collect DNS-challenge resolver defaults")
certificates = yaml.safe_load(read(f"{ROLE}/tasks/certificate-files.yml"))
pod_certificates = next(t for t in certificates if "ansible.builtin.command" in t)
assert pod_certificates["when"] == "caddy_kubernetes | bool"
assert pod_certificates["delegate_to"] == "{{ k8s_api_host }}"
assert "/data/caddy/certificates" in pod_certificates["ansible.builtin.command"]["argv"]
assert "stdout | length > 0" in pod_certificates["until"]
routes = read(f"{ROLE}/tasks/main/routes.yml")
assert routes.count("ansible.builtin.include_tasks: ../certificate-files.yml") == 2
assert defaults["hosting"]["method"] == "native" and "kubernetes" in defaults["hosting"]["supported"]
assert defaults["app"]["storage"] > 0 and defaults["backup"]["enabled"] is True

# Service: one LoadBalancer carrying the edge listeners and the admin API, client IP kept.
service = render("service.yaml.j2")[0]
assert service["spec"]["type"] == "LoadBalancer" and service["spec"]["externalTrafficPolicy"] == "Local"
assert [p["port"] for p in service["spec"]["ports"]] == [80, 443, 2019]
assert "annotations" not in service["metadata"]
pinned = render("service.yaml.j2", app_config={**defaults, "app": {**defaults["app"], "address": "192.0.2.50"}})[0]
assert pinned["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == "192.0.2.50"


def workload(modules):
    docs = {}
    for doc in render("manifest.yaml.j2", _caddy_modules_wanted=modules):
        docs.setdefault(doc["kind"], []).append(doc)
    return docs


plain = workload([])
assert {p["metadata"]["name"] for p in plain["PersistentVolumeClaim"]} == {"caddy-data", "caddy-config"}
for pvc in plain["PersistentVolumeClaim"]:
    assert "storageClassName" not in pvc["spec"]  # the cluster's default StorageClass
pod = plain["Deployment"][0]["spec"]["template"]["spec"]
assert "initContainers" not in pod and pod["containers"][0]["image"] == defaults["app"]["image"]
script = pod["containers"][0]["args"][0]
assert "--resume" in script and "exec caddy run" in script and "origins" in script
mounts = {m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
assert mounts == {"/etc/caddy", "/data", "/config"}

custom = workload(["github.com/caddy-dns/cloudflare"])
assert {p["metadata"]["name"] for p in custom["PersistentVolumeClaim"]} == {"caddy-data", "caddy-config", "caddy-bin"}
pod = custom["Deployment"][0]["spec"]["template"]["spec"]
build = pod["initContainers"][0]
assert build["image"] == defaults["app"]["image"] + "-builder"
assert "--with github.com/caddy-dns/cloudflare" in build["args"][0]
assert "exec /bin-cache/caddy-abc123 run" in pod["containers"][0]["args"][0]

# The role drives the shared operations with its own inputs and keeps no copy of them.
tasks = yaml.safe_load(read(f"{ROLE}/tasks/main/kubernetes.yml"))
backup = next(t for t in tasks if "recovery-backup.yml" in t.get("ansible.builtin.include_tasks", ""))
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "data", "claim": "{{ instance }}-data"}, {"name": "config", "claim": "{{ instance }}-config"}]
assert any("apply-manifest.yml" in t.get("ansible.builtin.include_tasks", "") for t in tasks)
config = read(f"{ROLE}/tasks/main/config.yml")
assert "apply-manifest.yml" in config and "sync-secret.yml" in config and "caddy_kubernetes" in config
restore = yaml.safe_load(read(f"{ROLE}/tasks/restore-kubernetes.yml"))
shared = next(t for t in restore if "recovery-restore.yml" in t.get("ansible.builtin.include_tasks", ""))
assert [a["claim"] for a in shared["vars"]["k8s_recovery"]["archives"]] == [
    "{{ restore_target }}-data", "{{ restore_target }}-config"]
assert shared["vars"]["k8s_recovery"]["stop"] == ["{{ restore_target }}"]
assert "restore-kubernetes.yml" in read(f"{ROLE}/tasks/restore.yml")

# The playbook keeps native and Docker on their guests and sends Kubernetes through the role.
play = yaml.safe_load(read("ansible/playbooks/apps/caddy.yml"))
kube = next(t for t in play[0]["tasks"] if t.get("name") == "Deploy Caddy on Kubernetes")
assert kube["ansible.builtin.include_role"]["name"] == "caddy"
assert "kubernetes" in kube["when"]
guest_deploy = next(t for t in play[1]["tasks"] if t.get("name") == "Deploy Caddy")
assert "'native', 'docker'" in guest_deploy["when"]
access = next(t for t in tasks if "open-app-access.yml" in t.get("ansible.builtin.include_tasks", ""))
assert access["vars"]["k8s_firewall_upstream_host"] == "{{ _caddy_bind_ip }}"
assert [t["name"] for t in tasks].index(access["name"]) > next(
    i for i, t in enumerate(tasks) if t["name"].startswith("Derive the admin endpoint"))
print("Caddy's Kubernetes method renders and dispatches as expected.")
