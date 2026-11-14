#!/usr/bin/env python3
"""Guard PostgreSQL's opt-in Kubernetes workload and its shared database and recovery path.

Checks: guests stay the default, the pod uses the default StorageClass and a MetalLB
LoadBalancer on the ingress address (no Ingress), the superuser is limited to the pod
network, the registry marks a Kubernetes backend, the shared database tasks run psql in
the server pod for it, and backup and restore go through the shared Kubernetes operations
and database step.
"""

from pathlib import Path
import subprocess

from jinja2 import Environment, StrictUndefined
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/postgresql"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


defaults = load("ansible/vars/app-defaults/postgresql.yml")["postgresql_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "native-vm", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["port"] == 5432
assert defaults["kubernetes"]["image"].endswith(":pg" + defaults["app"]["version"])
assert defaults["app"]["image"] == "postgres:" + defaults["app"]["version"]
assert defaults["kubernetes"]["superuser_cidr"] == "10.42.0.0/16"
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]
assert defaults["routing"] == {"proxy": "none", "identity": "none"}

# ── Playbook: Kubernetes is its own branch, the guest methods keep theirs ──────────────
playbook = load("ansible/playbooks/apps/postgresql.yml")
text = read("ansible/playbooks/apps/postgresql.yml")
provision = playbook[0]["tasks"]
assert any(
    t.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and t.get("when") == "app_config.hosting.method == 'kubernetes'"
    for t in provision
)
consumer_access = next(
    t for t in provision
    if str(t.get("ansible.builtin.include_tasks", "")).endswith("database/open-consumer-access.yml")
)
assert consumer_access["vars"]["database_consumer_provider"] == "postgresql"
assert any(
    str(t.get("ansible.builtin.include_tasks", "")).endswith("kubernetes/open-app-access.yml")
    for t in provision
)
assert any(
    t.get("when") == "app_config.hosting.method == 'native'"
    and "provision-backend-guest.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    for t in provision
)
# No application route, DNS record, Authentik app or monitor: the routing stays none.
assert not any(word in text.lower() for word in ("caddy", "authentik", "kuma", "dns"))
assert "generated_facts_data: \"{{ {instance: {'kubernetes': ''}} }}\"" in text
registry = next(
    t["vars"]["generated_facts_data"] for p in playbook for t in p.get("tasks", [])
    if t.get("vars", {}).get("generated_facts_service") == "databases"
    and t.get("when") == "app_config.hosting.method == 'kubernetes'"
)
registered = Environment(undefined=StrictUndefined).compile_expression(registry[3:-3])(
    instance="postgresql-test",
    homelabinfra_infra={"kubernetes": {"ingress_vip": "192.0.2.10"}},
    app_config={"app": {"port": 5433, "version": "16"}},
)
assert registered == {"postgresql-test": {
    "provider": "postgresql", "version": "16", "host": "192.0.2.10", "port": 5433,
    "runtime": "kubernetes", "kubernetes": {"namespace": "app-postgresql-test"},
}}

# ── Manifest ────────────────────────────────────────────────────────────────────────────
context = {
    "instance": "postgresql-test",
    "_postgresql_shared_buffers_mib": 256,
    "_postgresql_effective_cache_size_mib": 768,
    "_postgresql_work_mem_mib": 4,
    "_postgresql_config_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {
        "app": {"port": 5433, "storage": 8, "client_cidrs": ["10.0.0.0/8", "192.0.2.0/24"]},
        "kubernetes": {"image": "pgvector/pgvector:pg16", "superuser_cidr": "10.42.0.0/16"},
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
assert container["image"] == "pgvector/pgvector:pg16"
env = {e["name"]: e for e in container["env"]}
assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "superuser-password"
assert "POSTGRES_HOST_AUTH_METHOD" not in env
assert "hba_file=/etc/homelab-infra/pg_hba.conf" in container["args"]
assert "shared_buffers=256MB" in container["args"]
assert "-h" in container["readinessProbe"]["exec"]["command"]
mounts = {m["name"]: m for m in container["volumeMounts"]}
assert mounts["data"]["subPath"] == "data" and mounts["config"]["mountPath"] == "/etc/homelab-infra"
hba = [
    line.split() for line in objects["ConfigMap"]["data"]["pg_hba.conf"].splitlines()
    if line.startswith(("host", "local"))
]
# The superuser: the pod network first, then nowhere else; everyone else by client_cidrs.
assert hba[1] == ["host", "all", "postgres", "10.42.0.0/16", "scram-sha-256"]
assert hba[2] == ["host", "all", "postgres", "0.0.0.0/0", "reject"]
assert [row[3] for row in hba[3:]] == ["10.0.0.0/8", "192.0.2.0/24"]
assert all(row[-1] != "trust" for row in hba if row[0] == "host")
service = objects["Service"]
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 5433 and service["spec"]["ports"][0]["targetPort"] == 5432
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip
assert "Ingress" not in objects

# ── Deploy tasks ────────────────────────────────────────────────────────────────────────
deploy = load("ansible/roles/postgresql/tasks/kubernetes.yml")
names = [t["name"] for t in deploy]
assert names.index("PostgreSQL Kubernetes | Store the superuser credential") < names.index(
    "PostgreSQL Kubernetes | Apply the workload manifest"
)
assert any(str(t.get("ansible.builtin.include_tasks", "")).endswith("tasks/kubernetes/apply-manifest.yml")
           for t in deploy)
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
dispatch = load("ansible/roles/postgresql/tasks/restore.yml")
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml" for t in dispatch)
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-guest.yml" for t in dispatch)
restore = load("ansible/roles/postgresql/tasks/restore-kubernetes.yml")
job = next(
    t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
)
spec = job["vars"]["k8s_recovery"]
assert spec["live_server"] is True and spec["stop"] == []
# The restored access list only takes effect when the server restarts.
assert spec["restart"] == ["{{ restore_target }}"]
assert spec["archives"] == [{"name": "config", "config_map": "{{ restore_target }}-config"}]

# ── Shared database tasks reach a Kubernetes backend through its pod ───────────────────
psql = read("ansible/tasks/database/backend-psql.yml")
assert "exec" in psql and "deployment/{{ database_backend_instance }}" in psql
assert "--username=postgres" in psql and "--host" not in psql
for shared in ("provision-kubernetes-postgresql.yml", "drop.yml", "resolve-app-database.yml",
               "guard-app-database.yml"):
    assert "backend-psql.yml" in read(f"ansible/tasks/database/{shared}"), shared
provision_tasks = read("ansible/tasks/database/provision.yml")
assert "provision-postgresql.yml" in provision_tasks and "provision-kubernetes-postgresql.yml" in provision_tasks
assert "psql" not in provision_tasks  # the guest and Kubernetes halves live in their own files
for shared in ("drop.yml", "guard-app-database.yml"):
    assert "database_app_target.kubernetes | default(false) | bool" in read(f"ansible/tasks/database/{shared}")

# ── The server step: the whole cluster through one pg_dumpall script ───────────────────
step_tasks = load("ansible/tasks/database/kubernetes-recovery-step.yml")
server_block = next(t for t in step_tasks if t.get("when") == "k8s_database_step_source == 'namespace-server'")
read_task = next(t for t in server_block["block"] if t["name"].endswith("Read the server"))
variables = {
    "k8s_database_step_config": {"app": {"image": "postgres:16", "port": 5433}},
    "k8s_database_step_instance": "db", "k8s_database_step_root_password": "x",
}
templar = Templar(loader=DataLoader(), variables={**variables, **read_task["vars"]})
fact = templar.template(read_task["ansible.builtin.set_fact"], fail_on_undefined=True)
assert fact["_kds"]["engine"] == "postgresql" and fact["_kds"]["user"] == "postgres", fact
assert " ".join(fact["_kds"]["describe"].split()).startswith("the whole PostgreSQL server db")
describe = next(t for t in step_tasks if t["name"].endswith("Describe the server step"))
# The credential Secret's script lookup needs the role search path; the other keys do not.
step_spec = {k: v for k, v in describe["ansible.builtin.set_fact"]["k8s_database_step"].items()
             if k != "secret_data"}
step_fact = Templar(loader=DataLoader(), variables={"_kds": fact["_kds"]}).template(
    step_spec, fail_on_undefined=True)
assert step_fact["env"] == {
    "DB_ENGINE": "postgresql", "DB_HOST": "db", "DB_PORT": "5433", "DB_USER": "postgres",
}, step_fact["env"]
assert "postgresql.sql" in step_fact["dump"] and "server-load" in step_fact["load"]
client = read("ansible/tasks/database/scripts/database-client.sh")
assert "postgresql_server()" in client and "pg_dumpall" in client
# A load must keep the target's own superuser password and role, or the next connection of
# the same script (and every later Deploy) fails to authenticate.
assert "^CREATE ROLE postgres;$" in client and "ALTER ROLE postgres WITH" in client
subprocess.run(["sh", "-n", str(ROOT / "ansible/tasks/database/scripts/database-client.sh")], check=True)

# ── Provisioning for a Kubernetes backend is idempotent SQL in the pod ─────────────────
provision_k8s = load("ansible/tasks/database/provision-kubernetes-postgresql.yml")
create = next(t for t in provision_k8s if t["name"].endswith("Create or update the role and the database"))
sql = Environment(undefined=StrictUndefined, keep_trailing_newline=True).from_string(
    create["vars"]["database_backend_sql"]).render(
    _kpg_role="app_role", _kpg_name="app_db", _kpg_super="NOSUPERUSER", _database_password="pw",
    _database_existing_password="", _database_credential_action="reuse")
assert "WHERE NOT EXISTS (SELECT 1 FROM pg_roles" in sql and "WHERE NOT EXISTS (SELECT 1 FROM pg_database" in sql
assert "ALTER ROLE app_role PASSWORD 'pw'" in sql
assert "DATABASE app_db OWNER app_role" in sql

# ── No role-local copy of a shared operation ───────────────────────────────────────────
assert not (ROLE / "templates/kubernetes").exists()
assert not any("kubectl" in p.read_text(encoding="utf-8") and "apply" in p.read_text(encoding="utf-8")
               for p in (ROLE / "tasks").glob("*.yml") if p.name != "kubernetes.yml")
print("PostgreSQL keeps the guests as default and runs on Kubernetes through the shared operations")
