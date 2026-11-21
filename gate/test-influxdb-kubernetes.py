#!/usr/bin/env python3
"""Guard InfluxDB's opt-in Kubernetes workload and its shared database and recovery path.

Checks: guests stay the default, the pod uses the default StorageClass and a MetalLB
LoadBalancer on the ingress address (no Ingress), the wiring reaches that address, the
registry marks a Kubernetes backend, the registered consumers' firewall access, and backup
and restore go through the shared Kubernetes operations and database step (`influx backup`).
"""

import os
from pathlib import Path
import subprocess
import tempfile

from jinja2 import Environment, StrictUndefined
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/influxdb"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


defaults = load("ansible/vars/app-defaults/influxdb.yml")["influxdb_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "native-vm", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["port"] == 8086
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]

# ── Deploy: the shared deploy.yml with database-backend hooks ──────────────────────────
kube = load("ansible/roles/influxdb/tasks/kubernetes.yml")
consumer_access = next(
    t for t in kube
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("database/open-consumer-access.yml")
)
assert consumer_access["vars"]["database_consumer_provider"] == "influxdb"
assert "tasks/database/deploy-prepare.yml" in read("ansible/roles/influxdb/tasks/deploy-prepare.yml")
assert "tasks/database/deploy-record.yml" in read("ansible/roles/influxdb/tasks/deploy-record.yml")
# The route targets the API Service on the ingress address at app.port, as on guests.
assert defaults["deploy"]["kubernetes_upstream_port"] == "app"
assert "kubernetes_upstream_port | default('ingress') == 'app'" in read(
    "ansible/playbooks/apps/deploy.yml")

# ── Manifest ────────────────────────────────────────────────────────────────────────────
context = {
    "instance": "influx-test",
    "_influxdb_cache_max_memory_mib": 64,
    "_influxdb_cache_snapshot_memory_mib": 1,
    "_influxdb_config_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {"app": {
        "image": "influxdb:2.7", "port": 8087, "storage": 8, "admin_user": "homelab",
        "organization": "homelab", "bucket": "default",
    }},
}
environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
template = environment.from_string((ROLE / "templates/manifest.yaml.j2").read_text(encoding="utf-8"))
objects = {
    item["kind"]: item for item in yaml.safe_load_all(template.render(**context)) if item
}
assert {"ConfigMap", "PersistentVolumeClaim", "Deployment", "Service"} == set(objects)
# Shared restore recreates the ConfigMap with these ownership labels; Deploy must
# accept that shape without a corrective apply after recovery.
assert objects["ConfigMap"]["metadata"]["labels"] == {
    "app.kubernetes.io/instance": "influx-test",
    "app.kubernetes.io/managed-by": "homelab-infra",
}
assert objects["ConfigMap"]["data"]["INFLUXD_STORAGE_CACHE_MAX_MEMORY_SIZE"] == str(64 * 1024 * 1024)
pvc = objects["PersistentVolumeClaim"]["spec"]
assert "storageClassName" not in pvc and pvc["resources"]["requests"]["storage"] == "8Gi"
deployment = objects["Deployment"]["spec"]
assert deployment["strategy"]["type"] == "Recreate" and deployment["replicas"] == 1
pod = deployment["template"]["spec"]
container = pod["containers"][0]
assert container["image"] == "influxdb:2.7" and container["ports"] == [{"containerPort": 8086}]
env = {e["name"]: e for e in container["env"]}
# Secrets reach the pod only by reference.
for name, key in (("DOCKER_INFLUXDB_INIT_PASSWORD", "admin-password"),
                  ("DOCKER_INFLUXDB_INIT_ADMIN_TOKEN", "operator-token")):
    assert env[name]["valueFrom"]["secretKeyRef"] == {"name": "influx-test", "key": key}
assert env["DOCKER_INFLUXDB_INIT_MODE"]["value"] == "setup"
assert container["envFrom"] == [{"configMapRef": {"name": "influx-test-config"}}]
assert container["readinessProbe"]["httpGet"] == {"path": "/health", "port": 8086}
assert pod["initContainers"][0]["securityContext"] == {"runAsUser": 0}
service = objects["Service"]
selector = service["spec"]["selector"]
pod_labels = deployment["template"]["metadata"]["labels"]
assert all(pod_labels.get(key) == value for key, value in selector.items())
for component in ("backup", "restore"):
    recovery_labels = dict(pod_labels, **{"app.kubernetes.io/component": component})
    assert not all(recovery_labels.get(key) == value for key, value in selector.items())
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 8087 and service["spec"]["ports"][0]["targetPort"] == 8086
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip
assert "Ingress" not in objects

# ── Deploy tasks ────────────────────────────────────────────────────────────────────────
deploy = load("ansible/roles/influxdb/tasks/kubernetes.yml")
names = [t["name"] for t in deploy]
assert names.index("InfluxDB Kubernetes | Store the credentials") < names.index(
    "InfluxDB Kubernetes | Apply the workload manifest"
)
assert any(str(t.get("ansible.builtin.include_tasks", "")).endswith("tasks/kubernetes/apply-manifest.yml")
           for t in deploy)
guard = next(t for t in deploy if t["name"].endswith("Refuse to replace unavailable existing credentials"))
assert "_influxdb_pvc.rc != 0" in guard["ansible.builtin.assert"]["that"]
fingerprint = next(t for t in deploy if t["name"].endswith("Fingerprint the setup values, tuning and credentials"))
text = fingerprint["ansible.builtin.set_fact"]["_influxdb_config_checksum"]
# The ConfigMap is read once, at start: its tuning must change the pod template.
assert "_influxdb_cache_max_memory_mib" in text and "_influxdb_cache_snapshot_memory_mib" in text
backup = next(
    t for t in deploy if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
)
recovery = backup["vars"]["k8s_recovery"]
assert recovery["archives"] == [{"name": "config", "config_map": "{{ instance }}-config"}]
assert recovery["database"] == "{{ k8s_database_step | default({}) }}"
assert "stop" not in recovery
step = next(
    t for t in deploy
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("kubernetes-recovery-step.yml")
)
assert step["vars"]["k8s_database_step_source"] == "namespace-server"
assert step["vars"]["k8s_database_step_root_password"] == "{{ _influxdb_operator_token }}"

# ── Restore dispatches to the shared Job, which loads into the running server ──────────
dispatch = load("ansible/roles/influxdb/tasks/restore.yml")
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml" for t in dispatch)
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-guest.yml" for t in dispatch)
restore = load("ansible/roles/influxdb/tasks/restore-kubernetes.yml")
job = next(
    t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
)
spec = job["vars"]["k8s_recovery"]
assert spec["live_server"] is True and spec["stop"] == []
assert spec["restart"] == ["{{ restore_target }}"]
assert spec["archives"] == [{"name": "config", "config_map": "{{ restore_target }}-config"}]
# A new target's workload Secret and backup login follow the restored token.
assert spec["after_restore_tasks"].endswith("tasks/restore-credentials.yml")
creds = load("ansible/roles/influxdb/tasks/restore-credentials.yml")
assert any(t.get("vars", {}).get("k8s_secret_name") == "{{ restore_target }}" for t in creds)
patch = next(t for t in creds if t["name"].endswith("Point the backup login at the restored token"))
assert "db-password" in patch["ansible.builtin.command"]["stdin"] and "-backup" in str(patch["ansible.builtin.command"]["argv"])
inspect_step = next(t for t in restore if t["name"].endswith("Describe the target's server step"))
assert inspect_step["vars"]["k8s_database_step_restored_password"].endswith("source_credentials.operator_token }}")

# ── The server step: `influx backup` as one tar ────────────────────────────────────────
step_tasks = load("ansible/tasks/database/kubernetes-recovery-step.yml")
server_block = next(t for t in step_tasks if t.get("when") == "k8s_database_step_source == 'namespace-server'")
read_task = next(t for t in server_block["block"] if t["name"].endswith("Read the server"))
variables = {
    "k8s_database_step_config": {"app": {"image": "influxdb:2.7", "port": 8087}},
    "k8s_database_step_instance": "tsdb", "k8s_database_step_root_password": "target-token",
    "k8s_database_step_restored_password": "source-token",
}
templar = Templar(loader=DataLoader(), variables={**variables, **read_task["vars"]})
fact = templar.template(read_task["ansible.builtin.set_fact"], fail_on_undefined=True)
assert fact["_kds"]["engine"] == "influxdb", fact
assert " ".join(fact["_kds"]["describe"].split()).startswith("the whole InfluxDB server tsdb")
describe = next(t for t in step_tasks if t["name"].endswith("Describe the server step"))
raw_step = describe["ansible.builtin.set_fact"]["k8s_database_step"]
# The client script travels in secret_data through a file lookup, which Templar lacks here.
step_spec = {k: v for k, v in raw_step.items() if k != "secret_data"}
step_fact = Templar(loader=DataLoader(), variables={**variables, "_kds": fact["_kds"]}).template(
    step_spec, fail_on_undefined=True)
assert step_fact["env"] == {
    "DB_ENGINE": "influxdb", "DB_HOST": "tsdb", "DB_PORT": "8087", "DB_USER": "operator",
}, step_fact["env"]
assert step_fact["secret_env"] == {"DB_PASSWORD": "db-password", "DB_RESTORED_PASSWORD": "restored-password"}
assert "'restored-password': k8s_database_step_restored_password" in raw_step["secret_data"]
assert "influxdb.tar" in step_fact["dump"] and "server-load" in step_fact["load"]
without = Templar(loader=DataLoader(), variables={**variables, "k8s_database_step_restored_password": "",
                                                  "_kds": fact["_kds"]}).template(
    step_spec, fail_on_undefined=True)
assert without["secret_env"] == {"DB_PASSWORD": "db-password"}
script = ROOT / "ansible/tasks/database/scripts/database-client.sh"
client = script.read_text(encoding="utf-8")
assert "influxdb_server()" in client and "influx backup" in client and "restore" in client and "--full" in client
subprocess.run(["sh", "-n", str(script)], check=True)

# ── The client drives influx and fails when it fails ───────────────────────────────────
with tempfile.TemporaryDirectory(prefix="influxdb-client-") as tmp:
    bin_dir = Path(tmp)
    (bin_dir / "influx").write_text(
        '#!/bin/sh\necho "$*" >> "$CALLS"\n'
        '[ "$1" = "$FAIL_ON" ] && exit 1\n'
        'if [ "$1" = backup ]; then echo m > "$2/1.manifest"; fi\nexit 0\n')
    (bin_dir / "curl").write_text(
        '#!/bin/sh\nprintf \'{"buckets":[{"orgID":"a"},{"orgID":"a"}]}\'\n')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    calls = bin_dir / "calls"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DB_ENGINE": "influxdb",
           "DB_HOST": "tsdb", "DB_PORT": "8086", "DB_USER": "operator", "DB_PASSWORD": "tok", "CALLS": str(calls)}

    def run(action, fail_on="NONE", *args):
        return subprocess.run(["sh", str(script), action, *args], env={**env, "FAIL_ON": fail_on},
                              capture_output=True, text=True, timeout=120)

    point = bin_dir / "influxdb.tar"
    assert run("server-dump", "NONE", str(point)).returncode == 0 and point.stat().st_size > 0
    assert run("server-dump", "backup", str(bin_dir / "bad.tar")).returncode != 0
    assert not (bin_dir / "bad.tar").exists()
    assert run("server-verify", "NONE", str(point)).returncode == 0
    (bin_dir / "empty.tar").write_bytes(b"")
    assert run("server-verify", "NONE", str(bin_dir / "empty.tar")).returncode != 0
    calls.write_text("")
    assert run("server-load", "NONE", str(point)).returncode == 0
    restore_call = calls.read_text().strip()
    assert restore_call.startswith("restore ") and "--full" in restore_call and "--token tok" in restore_call
    assert run("server-load", "restore", str(point)).returncode != 0
    assert run("server-inspect").stdout.strip() == "2 buckets"

# ── Firewall: consumers name InfluxDB by influxdb_host and influxdb_port ───────────────
access = read("ansible/tasks/database/open-consumer-access.yml")
assert "'influxdb']" in access and "_sql_prefix ~ '_host'" in access
consumer = load("ansible/tasks/database/open-one-consumer-access.yml")
hosting = next(t["ansible.builtin.set_fact"]["_sql_consumer_k8s"] for t in consumer
               if "_sql_consumer_k8s" in t.get("ansible.builtin.set_fact", {}))
guest = consumer[-1]["vars"]["_consumer_guest"]
for method, expected in [("kubernetes", True), ("native", False), ("docker", False),
                         (None, False)]:
    inputs = {"ansible_facts": {"_sql_consumer_inputs": {"hosting": {"method": method}}}} if method else {}
    resolved = Templar(loader=DataLoader(), variables={"_sql_consumer_inputs_read": inputs}).template(hosting)
    assert resolved is expected, resolved
    variables = {"_sql_consumer_k8s": resolved, "tag_group_name": "old",
                 "groups": {"old": ["retained-one", "retained-two"] if expected else ["guest"]}}
    target = Templar(loader=DataLoader(), variables=variables).template(guest)
    assert target == ("" if expected else "guest"), target
assert consumer[-2]["when"] == "not (_sql_consumer_k8s | bool)"

# ── No role-local copy of a shared operation ───────────────────────────────────────────
assert not (ROLE / "templates/kubernetes").exists()
assert not any("kubectl" in p.read_text(encoding="utf-8") and "apply" in p.read_text(encoding="utf-8")
               for p in (ROLE / "tasks").glob("*.yml") if p.name != "kubernetes.yml")
# The drill reaches Kubernetes through the VIP, and guest methods through their host.
canary_tasks = yaml.safe_load((ROLE / "tasks/canary.yml").read_text())
api_inputs = next(t["ansible.builtin.set_fact"] for t in canary_tasks
                  if t["name"].endswith("Resolve API inputs"))
for kind, address in [("kubernetes", "192.0.2.30"), ("native", "192.0.2.40"),
                      ("docker", "192.0.2.40")]:
    result = Templar(loader=DataLoader(), variables={
        "_rat_kind": kind, "_rat_host": "target",
        "hostvars": {"target": {"ansible_host": "192.0.2.40"}},
        "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.30"}},
        "app_config": {"app": {"port": 8086}},
    }).template(api_inputs["_canary_url"], fail_on_undefined=True)
    assert result == f"http://{address}:8086", result

print("InfluxDB keeps the guests as default and runs on Kubernetes through the shared operations")
