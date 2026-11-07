#!/usr/bin/env python3
"""Guard Observability's opt-in Kubernetes workload and shared recovery path.

Checks: the single Prometheus+Grafana pod (Prometheus never published), the default
StorageClass, the shared backup/restore inputs, the playbook's method split, and that the
Docker default is unchanged.
"""

import hashlib
import json
from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/observability"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load((ROLE / path).read_text(encoding="utf-8"))


defaults = yaml.safe_load(read("ansible/vars/app-defaults/observability.yml"))[
    "observability_defaults"
]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]
assert defaults["app"]["grafana_storage"] > 0 and defaults["app"]["prometheus_storage"] > 0
assert defaults["backup"]["client"]["image"]
assert {"name": "node-exporter", "protocol": "TCP", "port": 9100} in defaults["firewall"]["egress"]

playbook = yaml.safe_load(read("ansible/playbooks/apps/observability.yml"))
provision = playbook[0]["tasks"]
assert any(
    task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and task.get("when") == "app_config.hosting.method == 'kubernetes'"
    for task in provision
)
assert any(
    task.get("ansible.builtin.import_tasks") == "../../tasks/stack/find-or-create-host.yml"
    and task.get("when") == "app_config.hosting.method != 'kubernetes'"
    for task in provision
)
assert "homelabinfra_infra.kubernetes.ingress_vip" in read(
    "ansible/playbooks/apps/observability.yml"
)

environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
environment.filters["to_json"] = json.dumps
environment.filters["hash"] = lambda value, algo: hashlib.new(algo, value.encode()).hexdigest()
config_files = {"prometheus.yml": "global: {}\n", "homelab-nodes.json": '{"a": 1}'}
context = {
    "instance": "obs-test",
    "observability_fqdn": "obs-test.example.test",
    "homelabinfra_config": {"timezone": "UTC"},
    "_observability_k8s_config": config_files,
    "app_config": {"app": dict(defaults["app"], retention="30d")},
}
workload = [
    item
    for item in yaml.safe_load_all(
        environment.from_string((ROLE / "templates/manifest.yaml.j2").read_text(
            encoding="utf-8"
        )).render(**context)
    )
    if item
]
objects = {(item["kind"], item["metadata"]["name"]): item for item in workload}
assert set(objects) == {
    ("ConfigMap", "obs-test-config"),
    ("PersistentVolumeClaim", "obs-test-prometheus"),
    ("PersistentVolumeClaim", "obs-test-grafana"),
    ("Deployment", "obs-test"),
    ("Service", "obs-test"),
    ("Ingress", "obs-test"),
}
assert json.loads(objects["ConfigMap", "obs-test-config"]["data"]["homelab-nodes.json"]) == {"a": 1}
for claim in ("obs-test-prometheus", "obs-test-grafana"):
    assert "storageClassName" not in objects["PersistentVolumeClaim", claim]["spec"]
deployment = objects["Deployment", "obs-test"]
pod = deployment["spec"]["template"]["spec"]
assert deployment["spec"]["strategy"]["type"] == "Recreate"
assert pod["automountServiceAccountToken"] is False
containers = {c["name"]: c for c in pod["containers"]}
assert set(containers) == {"prometheus", "grafana"}
assert "--web.listen-address=127.0.0.1:9090" in containers["prometheus"]["args"]
assert "ports" not in containers["prometheus"]
service = objects["Service", "obs-test"]["spec"]
assert [p["targetPort"] for p in service["ports"]] == [3000]
assert objects["Ingress", "obs-test"]["spec"]["rules"][0]["host"] == "obs-test.example.test"
assert deployment["spec"]["template"]["metadata"]["annotations"][
    "homelab-infra/config-sha256"
]

deploy = load("tasks/kubernetes.yml")
backup = next(
    task for task in deploy
    if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
)
assert backup["vars"]["k8s_recovery"]["archives"] == [
    {"name": "grafana", "claim": "{{ instance }}-grafana"},
    {"name": "prometheus", "claim": "{{ instance }}-prometheus"},
]
assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

dispatch = load("tasks/restore.yml")
assert any(
    str(task.get("ansible.builtin.include_tasks", "")).endswith("restore-kubernetes.yml")
    for task in dispatch
)
restore = load("tasks/restore-kubernetes.yml")
recovery = restore[0]["vars"]["k8s_recovery"]
assert recovery["archives"] == [
    {"name": "grafana", "claim": "{{ restore_target }}-grafana", "owner": "472:472"},
    {"name": "prometheus", "claim": "{{ restore_target }}-prometheus", "owner": "65534:472"},
]
assert recovery["stop"] == ["{{ restore_target }}"]
assert "recovery-restore.yml" in restore[0]["ansible.builtin.include_tasks"]
assert not (ROLE / "templates/kubernetes").exists()

print("observability kubernetes: ok")
