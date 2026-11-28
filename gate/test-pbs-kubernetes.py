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
                 ("PersistentVolumeClaim", "pbs-datastore"), ("NetworkPolicy", "pbs-listener"), ("Deployment", "pbs")], \
    "no Service selects the pod while the factory password is valid: " + str(kinds)
bound = yaml.safe_load_all(env.get_template("manifest.yaml.j2").render(
    instance="pbs", app_config=defaults, pbs_backup_uid=34, pbs_listener_open=False,
    pbs_retained_volumes={"datastore": "pvc-1234"},
    homelabinfra_config={"infrastructure": {"backups": {"datastore_path": "/mnt/backup"}}},
    homelabinfra_infra={"kubernetes": {"ingress_vip": "192.0.2.50"}}))
claims = {d["metadata"]["name"]: d["spec"] for d in bound if d and d["kind"] == "PersistentVolumeClaim"}
assert claims["pbs-datastore"]["volumeName"] == "pvc-1234" and "volumeName" not in claims["pbs-config"]
assert all("storageClassName" not in doc["spec"] for doc in closed if doc["kind"] == "PersistentVolumeClaim"), \
    "PBS uses the cluster's default StorageClass"
deployment = next(doc for doc in closed if doc["kind"] == "Deployment")["spec"]
assert deployment["strategy"]["type"] == "Recreate"
pod = deployment["template"]["spec"]
mounts = {m["name"]: m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
assert mounts["datastore"] == "/mnt/backup" and mounts["config"] == "/etc/proxmox-backup", mounts
assert "chmod 0755 '/mnt/backup'" in pod["initContainers"][0]["command"][2]

assert next(d for d in closed if d["kind"] == "NetworkPolicy")["spec"]["ingress"] == []
opened = render(True)
assert next(d for d in opened if d["kind"] == "NetworkPolicy")["spec"]["ingress"] == [{}]
lan = next(doc for doc in opened if doc["metadata"]["name"] == "pbs-lan")
assert lan["spec"]["type"] == "LoadBalancer" and lan["spec"]["ports"][0]["port"] == 8007, lan
assert lan["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == "192.0.2.50"
assert not any(doc["kind"] == "Ingress" for doc in opened)

deploy = (ROLE / "tasks/kubernetes.yml").read_text()
assert "tasks/kubernetes/apply-manifest.yml" in deploy
tasks = yaml.safe_load(deploy)
sched = (ROLE / "tasks/schedule-backup.yml").read_text()
backup = next(t for t in yaml.safe_load(sched) if "Schedule the application backup" in t["name"])
assert backup["vars"]["k8s_recovery"]["archives"] == [{"name": "config", "claim": "{{ instance }}-config"}]
restore = yaml.safe_load((ROLE / "tasks/restore-kubernetes.yml").read_text())[0]
recovery = restore["vars"]["k8s_recovery"]
assert restore["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/recovery-restore.yml")
assert recovery["archives"][0]["claim"] == "{{ restore_target }}-config" and recovery["stop"] == ["{{ restore_target }}"]
# The default password is replaced over the pod's loopback, before any Service exists.
assert "pbs_api_via_pod: true" in deploy
assert deploy.index("Replace the image's default administrator password") < deploy.index("Publish the listener")
assert "retained-volumes.yml" in deploy and "recovery-backup.yml" not in deploy
sched = (ROLE / "tasks/schedule-backup.yml").read_text()
assert "tasks/kubernetes/recovery-backup.yml" in sched
play = (ROOT / "ansible/playbooks/apps/pbs.yml").read_text()
assert play.index("Configure datastore, backup job and notifications") < play.index("tasks_from: schedule-backup")
assert "restore-kubernetes.yml" in (ROLE / "tasks/restore.yml").read_text()

# A PBS restoring its own configuration reads the recovery point from itself: the archive is
# staged onto the claim while the pod runs, and the Job after the stop only moves it into place.
assert recovery["stage_first"] is True
shared = (ROOT / "ansible/tasks/kubernetes/recovery-restore.yml").read_text()
assert shared.index("Stage the archives onto the target's volumes") < shared.index("Stop the target application")
restore_env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "ansible/templates/kubernetes"),
                                 undefined=jinja2.StrictUndefined, extensions=["jinja2.ext.do"])
restore_env.filters["bool"] = bool
restore_env.filters["to_json"] = env.filters["to_json"]
client = {"image": "img", "key_url": "u", "repository": "r", "suite": "s", "component": "c"}
base = {"target": "pbs", "timeout": 900, "secret": "pbs-restore", "backup_id": "pbs", "snapshot": "host/pbs/x",
        "scratch_gi": 2, "client": client, "database": {}, "stage_first": True,
        "archives": [{"name": "config", "claim": "pbs-config", "owner": "34:34"}]}
for mode in ("stage", "restore"):
    job = yaml.safe_load(restore_env.get_template("recovery-restore.yaml.j2").render(
        _kr={**base, "mode": mode, "job_name": "pbs-" + mode}, k8s_pbs_repository="t@pbs:s", k8s_pbs_fingerprint="f"))
    containers = job["spec"]["template"]["spec"]["containers"]
    script = containers[0]["args"][0]
    if mode == "stage":
        assert "restore \"${SNAPSHOT}\" config.pxar /volumes/config/.restore-stage" in script
    else:
        assert "config.pxar" not in script and "mv {} /volumes/config/" in script, script

# The API calls Deploy shares with the other methods must delegate to the cluster node.
for name in ("token.yml", "api-wait.yml", "api-call.yml", "register-reused-datastore.yml"):
    for task in yaml.safe_load((ROLE / "tasks" / name).read_text()):
        for item in [task, *task.get("block", []), *task.get("rescue", [])]:
            if "ansible.builtin.uri" in item or "ansible.builtin.command" in item:
                assert "pbs_exec_host" in item.get("delegate_to", ""), (name, item["name"])

playbook = (ROOT / "ansible/playbooks/apps/pbs.yml").read_text()
assert "tasks_from: kubernetes" in playbook and "tasks/kubernetes/open-app-access.yml" in playbook
print("PBS Kubernetes method renders as expected.")
sys.exit(0)
