#!/usr/bin/env python3
"""Guard MySQL's opt-in Kubernetes workload and its shared database and recovery path.

Checks: guests stay the default, the pod uses the default StorageClass and a MetalLB
LoadBalancer on the ingress address (no Ingress), the registry marks a Kubernetes backend,
the shared database tasks run SQL in the server pod for it, and backup and restore go
through the shared Kubernetes operations and database step.
"""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/mysql"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


defaults = load("ansible/vars/app-defaults/mysql.yml")["mysql_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "native-vm", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["port"] == 3306
assert defaults["kubernetes"]["root_host"] == "10.42.%"
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]
assert defaults["routing"]["proxy"] == "none"

# ── Playbook: Kubernetes is its own branch, the guest methods keep theirs ──────────────
playbook = load("ansible/playbooks/apps/mysql.yml")
text = read("ansible/playbooks/apps/mysql.yml")
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
# Ansible templates mapping values, not keys: evaluate the complete registry mapping.
registry = next(
    t["vars"]["generated_facts_data"] for p in playbook for t in p.get("tasks", [])
    if t.get("vars", {}).get("generated_facts_service") == "databases"
    and t.get("when") == "app_config.hosting.method == 'kubernetes'"
)
registered = Environment(undefined=StrictUndefined).compile_expression(registry[3:-3])(
    instance="mysql-test",
    homelabinfra_infra={"kubernetes": {"ingress_vip": "192.0.2.10"}},
    app_config={"app": {"port": 3307, "client_hosts": ["192.0.2.%"]}},
)
assert registered == {"mysql-test": {
    "provider": "mysql", "host": "192.0.2.10", "port": 3307,
    "client_hosts": ["192.0.2.%"], "kubernetes": {"namespace": "app-mysql-test"},
}}

# ── Manifest ────────────────────────────────────────────────────────────────────────────
context = {
    "instance": "mysql-test",
    "_mysql_innodb_buffer_pool_size_mib": 512,
    "_mysql_config_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {
        "app": {"image": "mysql:8.4", "port": 3307, "storage": 8},
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
assert env["MYSQL_ROOT_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "root-password"
assert env["MYSQL_ROOT_HOST"]["value"] == "10.42.%"
assert "MYSQL_ALLOW_EMPTY_PASSWORD" not in env
assert "@@skip_networking" in container["readinessProbe"]["exec"]["command"][-1]
assert {m["mountPath"] for m in container["volumeMounts"]} == {"/var/lib/mysql", "/etc/mysql/conf.d"}
# A volume root may hold lost+found, which MySQL's first initialization refuses.
assert {m["name"]: m.get("subPath") for m in container["volumeMounts"]}["data"] == "data"
service = objects["Service"]
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 3307 and service["spec"]["ports"][0]["targetPort"] == 3306
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip
assert "Ingress" not in objects

# ── Deploy tasks ────────────────────────────────────────────────────────────────────────
deploy = load("ansible/roles/mysql/tasks/kubernetes.yml")
names = [t["name"] for t in deploy]
assert names.index("MySQL Kubernetes | Store the root credential") < names.index(
    "MySQL Kubernetes | Apply the workload manifest"
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
assert step["vars"]["k8s_database_step_source"] == "namespace-server"

# ── Restore dispatches to the shared Job, which loads into the running server ──────────
dispatch = load("ansible/roles/mysql/tasks/restore.yml")
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml" for t in dispatch)
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-guest.yml" for t in dispatch)
restore = load("ansible/roles/mysql/tasks/restore-kubernetes.yml")
job = next(
    t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
)
spec = job["vars"]["k8s_recovery"]
assert spec["live_server"] is True and spec["stop"] == []
assert spec["archives"] == [{"name": "config", "config_map": "{{ restore_target }}-config"}]
assert "k8s_recovery.live_server" in read("ansible/tasks/kubernetes/recovery-restore.yml")

# ── Shared database tasks reach a Kubernetes backend through its pod ───────────────────
sql = read("ansible/tasks/database/backend-sql.yml")
assert "exec" in sql and "deployment/{{ database_backend_instance }}" in sql
assert "--protocol=socket" in sql
assert "MARIADB_ROOT_PASSWORD" in sql and "MYSQL_ROOT_PASSWORD" in sql and "exec mysql " in sql
for shared in ("provision-kubernetes-sql.yml", "drop.yml", "resolve-app-database.yml"):
    assert "backend-sql.yml" in read(f"ansible/tasks/database/{shared}"), shared
mysql = read("ansible/tasks/database/provision-mysql-compatible.yml")
assert mysql.count("_database_backend.kubernetes | default({}) | length == 0") == 2
assert "provision-kubernetes-sql.yml" in mysql and "provider == 'mariadb'" not in mysql
assert "database_app_target.kubernetes | default(false) | bool" in read("ansible/tasks/database/guard-app-database.yml")
client = read("ansible/tasks/database/scripts/database-client.sh")
assert 'DB_ENGINE: "{{ _kds.engine }}"' in read("ansible/tasks/database/kubernetes-recovery-step.yml")
for action in ("server-dump", "server-verify", "server-load", "server-inspect"):
    assert action in client, action
step_file = read("ansible/tasks/database/kubernetes-recovery-step.yml")
assert "namespace-server" in step_file and "server-load" in step_file

# ── No role-local copy of a shared operation ───────────────────────────────────────────
assert not (ROLE / "templates/kubernetes").exists()
assert not any("kubectl" in p.read_text(encoding="utf-8") and "apply" in p.read_text(encoding="utf-8")
               for p in (ROLE / "tasks").glob("*.yml") if p.name not in ("kubernetes.yml",))
# The consumer loop's variable must be the one the included file reads: resolve every
# variable of its tasks for one consumer, with only that loop variable and the inputs given.
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

loop_var = load("ansible/tasks/database/open-consumer-access.yml")[0]["block"][1]["loop_control"]["loop_var"]
templar = Templar(loader=DataLoader(), variables={loop_var: "bookstack", "instance": "mysql-test"})
for task in load("ansible/tasks/database/open-one-consumer-access.yml"):
    values = [task["name"], *task.get("vars", {}).values()]
    values = [v for v in values if "_consumer_guest" not in str(v) and "database_consumer_provider" not in str(v)]
    for value in values:
        assert "_mariadb_consumer" not in str(value)
        templar.template(value, fail_on_undefined=True)
# A fresh namespace-server invocation has no `_kds`: the fact it sets must not read itself.
step_tasks = load("ansible/tasks/database/kubernetes-recovery-step.yml")
server_block = next(t for t in step_tasks if t.get("when") == "k8s_database_step_source == 'namespace-server'")
read_task = next(t for t in server_block["block"] if t["name"].endswith("Read the server"))
for image, label in (("mysql:8.4", "the whole MySQL server"), ("mariadb:11.8", "the whole MariaDB server")):
    variables = {"k8s_database_step_config": {"app": {"image": image, "port": 3306}},
                 "k8s_database_step_instance": "db", "k8s_database_step_root_password": "x"}
    fact = Templar(loader=DataLoader(), variables={**variables, **read_task["vars"]}).template(
        read_task["ansible.builtin.set_fact"], fail_on_undefined=True)["_kds"]
    assert " ".join(fact["describe"].split()).startswith(label), fact["describe"]
print("MySQL keeps the guests as default and runs on Kubernetes through the shared operations")

