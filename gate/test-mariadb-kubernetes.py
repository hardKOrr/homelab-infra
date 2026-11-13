#!/usr/bin/env python3
"""Guard MariaDB's opt-in Kubernetes workload and its shared database and recovery path.

Checks: guests stay the default, the pod uses the default StorageClass and a MetalLB
LoadBalancer on the ingress address (no Ingress), the registry marks a Kubernetes backend,
the shared database tasks run SQL in the server pod for it, and backup and restore go
through the shared Kubernetes operations and database step.
"""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/mariadb"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


defaults = load("ansible/vars/app-defaults/mariadb.yml")["mariadb_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "native-vm", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["port"] == 3306
assert defaults["kubernetes"]["root_host"] == "10.42.%"
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]
assert defaults["routing"]["proxy"] == "none"

# ── Playbook: Kubernetes is its own branch, the guest methods keep theirs ──────────────
playbook = load("ansible/playbooks/apps/mariadb.yml")
text = read("ansible/playbooks/apps/mariadb.yml")
provision = playbook[0]["tasks"]
assert any(
    t.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and t.get("when") == "app_config.hosting.method == 'kubernetes'"
    for t in provision
)
assert any(
    str(t.get("ansible.builtin.include_tasks", "")).endswith("database/open-consumer-access.yml")
    for t in provision
)
assert any(
    t.get("when") == "app_config.hosting.method == 'native'"
    and "provision-backend-guest.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    for t in provision
)
# The registry marks a Kubernetes backend, and clears the mark when it stops being one.
assert "homelabinfra_infra.kubernetes.ingress_vip" in text
assert "generated_facts_data: \"{{ {instance: {'kubernetes': ''}} }}\"" in text
assert 'namespace: "app-{{ instance }}"' in text

# ── Manifest ────────────────────────────────────────────────────────────────────────────
context = {
    "instance": "mariadb-test",
    "_mariadb_innodb_buffer_pool_size_mib": 512,
    "_mariadb_config_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {
        "app": {"image": "mariadb:11.8", "port": 3307, "storage": 8},
        "kubernetes": {"root_host": "10.42.%"},
    },
}
environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
template = environment.from_string((ROLE / "templates/manifest.yaml.j2").read_text(encoding="utf-8"))
objects = {
    item["kind"]: item for item in yaml.safe_load_all(template.render(**context)) if item
}
assert {"ConfigMap", "PersistentVolumeClaim", "Deployment", "Service"} == set(objects)
pvc = objects["PersistentVolumeClaim"]["spec"]
assert "storageClassName" not in pvc and pvc["resources"]["requests"]["storage"] == "8Gi"
deployment = objects["Deployment"]["spec"]
assert deployment["strategy"]["type"] == "Recreate" and deployment["replicas"] == 1
container = deployment["template"]["spec"]["containers"][0]
env = {e["name"]: e for e in container["env"]}
assert env["MARIADB_ROOT_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "root-password"
assert env["MARIADB_ROOT_HOST"]["value"] == "10.42.%"
assert "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD" not in env
assert "@@skip_networking" in container["readinessProbe"]["exec"]["command"][-1]
assert {m["mountPath"] for m in container["volumeMounts"]} == {"/var/lib/mysql", "/etc/mysql/conf.d"}
service = objects["Service"]
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 3307 and service["spec"]["ports"][0]["targetPort"] == 3306
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip
assert "Ingress" not in objects

# ── Deploy tasks ────────────────────────────────────────────────────────────────────────
deploy = load("ansible/roles/mariadb/tasks/kubernetes.yml")
names = [t["name"] for t in deploy]
assert names.index("MariaDB Kubernetes | Store the root credential") < names.index(
    "MariaDB Kubernetes | Apply the workload manifest"
)
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
assert step["vars"]["k8s_database_step_source"] == "namespace-mariadb-server"

# ── Restore dispatches to the shared Job, which loads into the running server ──────────
dispatch = load("ansible/roles/mariadb/tasks/restore.yml")
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml" for t in dispatch)
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-guest.yml" for t in dispatch)
restore = load("ansible/roles/mariadb/tasks/restore-kubernetes.yml")
job = next(
    t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
)
spec = job["vars"]["k8s_recovery"]
assert spec["live_server"] is True and "stop" not in spec
assert spec["archives"] == [{"name": "config", "config_map": "{{ restore_target }}-config"}]
assert "k8s_recovery.live_server" in read("ansible/tasks/kubernetes/recovery-restore.yml")

# ── Shared database tasks reach a Kubernetes backend through its pod ───────────────────
sql = read("ansible/tasks/database/backend-sql.yml")
assert "exec" in sql and "deployment/{{ database_backend_instance }}" in sql
assert "--protocol=socket" in sql and "MARIADB_ROOT_PASSWORD" in sql
for shared in ("provision-kubernetes-mariadb.yml", "drop.yml", "resolve-app-database.yml"):
    assert "backend-sql.yml" in read(f"ansible/tasks/database/{shared}"), shared
mysql = read("ansible/tasks/database/provision-mysql-compatible.yml")
assert mysql.count("_database_backend.kubernetes | default({}) | length == 0") == 2
assert "provision-kubernetes-mariadb.yml" in mysql
assert "database_app_target.kubernetes | default(false) | bool" in read("ansible/tasks/database/guard-app-database.yml")
client = read("ansible/tasks/database/scripts/database-client.sh")
for action in ("server-dump", "server-verify", "server-load", "server-inspect"):
    assert action in client, action
step_file = read("ansible/tasks/database/kubernetes-recovery-step.yml")
assert "namespace-mariadb-server" in step_file and "server-load" in step_file

# ── No role-local copy of a shared operation ───────────────────────────────────────────
assert not (ROLE / "templates/kubernetes").exists()
assert not any("kubectl" in p.read_text(encoding="utf-8") and "apply" in p.read_text(encoding="utf-8")
               for p in (ROLE / "tasks").glob("*.yml") if p.name not in ("kubernetes.yml",))
print("MariaDB keeps the guests as default and runs on Kubernetes through the shared operations")

# A set_fact cannot read a fact it defines itself: the ownership guard's intermediate
# list is a task var, never a sibling key.
guard = load("ansible/tasks/database/guard-app-database.yml")[0]
assert "_other_ports" in guard["vars"]
assert "_other_ports" not in guard["ansible.builtin.set_fact"]
