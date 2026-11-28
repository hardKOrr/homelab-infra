#!/usr/bin/env python3
"""PBS's Kubernetes method renders one Deployment with its three volumes, keeps the listener
closed until the default password is replaced, and backs up and restores through the shared
recovery tasks."""
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/pbs"
env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROLE / "templates"), undefined=jinja2.StrictUndefined)
env.filters["quote"] = lambda text: "'" + str(text) + "'"
env.filters["to_json"] = lambda value: __import__("json").dumps(value)
env.filters["bool"] = bool
defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/pbs.yml").read_text())["pbs_defaults"]
assert "kubernetes" in defaults["hosting"]["supported"] and defaults["hosting"]["method"] == "native"


def render(open_listener):
    text = env.get_template("manifest.yaml.j2").render(
        instance="pbs", app_config=defaults, pbs_backup_uid=34, pbs_listener_open=open_listener,
        homelabinfra_config={"infrastructure": {"backups": {"datastore_path": "/mnt/backup"}}},
        homelabinfra_infra={"kubernetes": {"ingress_vip": "192.0.2.50"}},
    )
    return [doc for doc in yaml.safe_load_all(text) if doc]


closed = render(False)
kinds = [(doc["kind"], doc["metadata"]["name"]) for doc in closed]
assert kinds == [("PersistentVolumeClaim", "pbs-config"), ("PersistentVolumeClaim", "pbs-state"),
                 ("PersistentVolumeClaim", "pbs-datastore"), ("Deployment", "pbs"), ("Service", "pbs")], kinds
assert all("storageClassName" not in doc["spec"] for doc in closed if doc["kind"] == "PersistentVolumeClaim"), \
    "PBS uses the cluster's default StorageClass"
deployment = next(doc for doc in closed if doc["kind"] == "Deployment")["spec"]
assert deployment["strategy"]["type"] == "Recreate"
pod = deployment["template"]["spec"]
mounts = {m["name"]: m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
assert mounts["datastore"] == "/mnt/backup" and mounts["config"] == "/etc/proxmox-backup", mounts
assert "chmod 0755 '/mnt/backup'" in pod["initContainers"][0]["command"][2]

opened = render(True)
lan = next(doc for doc in opened if doc["metadata"]["name"] == "pbs-lan")
assert lan["spec"]["type"] == "LoadBalancer" and lan["spec"]["ports"][0]["port"] == 8007, lan
assert lan["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == "192.0.2.50"
assert not any(doc["kind"] == "Ingress" for doc in opened)

deploy = (ROLE / "tasks/kubernetes.yml").read_text()
for shared in ("tasks/kubernetes/apply-manifest.yml", "tasks/kubernetes/recovery-backup.yml"):
    assert shared in deploy, shared
tasks = yaml.safe_load(deploy)
backup = next(t for t in tasks if "Schedule the application backup" in t["name"])
assert backup["vars"]["k8s_recovery"]["archives"] == [{"name": "config", "claim": "{{ instance }}-config"}]
restore = yaml.safe_load((ROLE / "tasks/restore-kubernetes.yml").read_text())[0]
recovery = restore["vars"]["k8s_recovery"]
assert restore["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/recovery-restore.yml")
assert recovery["archives"][0]["claim"] == "{{ restore_target }}-config" and recovery["stop"] == ["{{ restore_target }}"]
assert "restore-kubernetes.yml" in (ROLE / "tasks/restore.yml").read_text()

# The API calls Deploy shares with the other methods must delegate to the cluster node.
for name in ("token.yml", "api-wait.yml", "admin-password.yml", "register-reused-datastore.yml"):
    for task in yaml.safe_load((ROLE / "tasks" / name).read_text()):
        for item in [task, *task.get("block", []), *task.get("rescue", [])]:
            if "ansible.builtin.uri" in item or "ansible.builtin.command" in item:
                assert "pbs_exec_host" in item.get("delegate_to", ""), (name, item["name"])

playbook = (ROOT / "ansible/playbooks/apps/pbs.yml").read_text()
assert "tasks_from: kubernetes" in playbook and "tasks/kubernetes/open-app-access.yml" in playbook
print("PBS Kubernetes method renders as expected.")
sys.exit(0)
