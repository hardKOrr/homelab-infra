#!/usr/bin/env python3
"""Guard Redis's opt-in Kubernetes workload and its shared database and recovery path.

Checks: guests stay the default, the pod uses the default StorageClass and a MetalLB
LoadBalancer on the ingress address (no Ingress), the registry marks a Kubernetes backend,
the registered consumers' firewall access, and backup and restore go through the shared
Kubernetes operations and database step (RDB plus ACL users).
"""

from pathlib import Path
import subprocess

from jinja2 import Environment, StrictUndefined
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/redis"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def load(path):
    return yaml.safe_load(read(path))


defaults = load("ansible/vars/app-defaults/redis.yml")["redis_defaults"]
assert defaults["hosting"]["method"] == "native"
assert defaults["hosting"]["supported"] == [
    "native-lxc", "native-vm", "docker-lxc", "docker-vm", "kubernetes",
]
assert defaults["app"]["storage"] > 0 and defaults["app"]["port"] == 6379
assert defaults["backup"]["client"]["image"] and defaults["backup"]["schedule"]
assert defaults["routing"] == {"proxy": "none", "identity": "none"}

# ── Playbook: Kubernetes is its own branch, the guest methods keep theirs ──────────────
playbook = load("ansible/playbooks/apps/redis.yml")
text = read("ansible/playbooks/apps/redis.yml")
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
assert consumer_access["vars"]["database_consumer_provider"] == "redis"
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
registry = next(
    t["vars"]["generated_facts_data"] for p in playbook for t in p.get("tasks", [])
    if t.get("vars", {}).get("generated_facts_service") == "databases"
    and t.get("when") == "app_config.hosting.method == 'kubernetes'"
)
registered = Environment(undefined=StrictUndefined).compile_expression(registry[3:-3])(
    instance="redis-test",
    homelabinfra_infra={"kubernetes": {"ingress_vip": "192.0.2.10"}},
    app_config={"app": {"port": 6380}},
)
assert registered == {"redis-test": {
    "provider": "redis", "host": "192.0.2.10", "port": 6380,
    "runtime": "kubernetes", "kubernetes": {"namespace": "app-redis-test"},
}}

# ── Manifest ────────────────────────────────────────────────────────────────────────────
context = {
    "instance": "redis-test",
    "_redis_maxmemory_mib": 256,
    "_redis_maxmemory_policy": "noeviction",
    "_redis_config_checksum": "abc",
    "homelabinfra_config": {"timezone": "UTC"},
    "homelabinfra_infra": {"kubernetes": {"ingress_vip": "192.0.2.10"}},
    "app_config": {"app": {"image": "redis:7", "port": 6380, "storage": 2}},
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
    "app.kubernetes.io/instance": "redis-test",
    "app.kubernetes.io/managed-by": "homelab-infra",
}
conf = objects["ConfigMap"]["data"]["redis.conf"]
assert "aclfile /data/users.acl" in conf and "maxmemory 256mb" in conf and "protected-mode yes" in conf
pvc = objects["PersistentVolumeClaim"]["spec"]
assert "storageClassName" not in pvc and pvc["resources"]["requests"]["storage"] == "2Gi"
deployment = objects["Deployment"]["spec"]
assert deployment["strategy"]["type"] == "Recreate" and deployment["replicas"] == 1
pod = deployment["template"]["spec"]
container = pod["containers"][0]
assert container["image"] == "redis:7" and container["args"] == ["/etc/homelab-infra/redis.conf"]
assert container["ports"] == [{"containerPort": 6379}]
env = {e["name"]: e for e in pod["initContainers"][0]["env"]}
assert env["PASSWORD_SHA256"]["valueFrom"]["secretKeyRef"] == {"name": "redis-test", "key": "password-sha256"}
# The password itself never reaches a manifest or the process list.
assert "requirepass" not in conf and "PASSWORD" not in {e["name"] for e in container["env"]}
service = objects["Service"]
selector = service["spec"]["selector"]
pod_labels = deployment["template"]["metadata"]["labels"]
assert all(pod_labels.get(key) == value for key, value in selector.items())
for component in ("backup", "restore"):
    recovery_labels = dict(pod_labels, **{"app.kubernetes.io/component": component})
    assert not all(recovery_labels.get(key) == value for key, value in selector.items())
vip = context["homelabinfra_infra"]["kubernetes"]["ingress_vip"]
assert service["spec"]["type"] == "LoadBalancer"
assert service["spec"]["ports"][0]["port"] == 6380 and service["spec"]["ports"][0]["targetPort"] == 6379
assert service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"] == vip
assert service["metadata"]["annotations"]["metallb.io/allow-shared-ip"] == vip
assert "Ingress" not in objects

# ── Deploy tasks ────────────────────────────────────────────────────────────────────────
deploy = load("ansible/roles/redis/tasks/kubernetes.yml")
names = [t["name"] for t in deploy]
assert names.index("Redis Kubernetes | Store the backend password") < names.index(
    "Redis Kubernetes | Apply the workload manifest"
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
dispatch = load("ansible/roles/redis/tasks/restore.yml")
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-kubernetes.yml" for t in dispatch)
assert any(str(t.get("ansible.builtin.include_tasks", "")) == "restore-guest.yml" for t in dispatch)
restore = load("ansible/roles/redis/tasks/restore-kubernetes.yml")
job = next(
    t for t in restore if str(t.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
)
spec = job["vars"]["k8s_recovery"]
assert spec["live_server"] is True and spec["stop"] == []
assert spec["restart"] == ["{{ restore_target }}"]
assert spec["archives"] == [{"name": "config", "config_map": "{{ restore_target }}-config"}]

# ── The server step: the data set as an RDB beside its ACL users ───────────────────────
step_tasks = load("ansible/tasks/database/kubernetes-recovery-step.yml")
server_block = next(t for t in step_tasks if t.get("when") == "k8s_database_step_source == 'namespace-server'")
read_task = next(t for t in server_block["block"] if t["name"].endswith("Read the server"))
variables = {
    "k8s_database_step_config": {"app": {"image": "redis:7", "port": 6380}},
    "k8s_database_step_instance": "cache", "k8s_database_step_root_password": "x",
}
templar = Templar(loader=DataLoader(), variables={**variables, **read_task["vars"]})
fact = templar.template(read_task["ansible.builtin.set_fact"], fail_on_undefined=True)
assert fact["_kds"]["engine"] == "redis" and fact["_kds"]["user"] == "default", fact
assert " ".join(fact["_kds"]["describe"].split()).startswith("the whole Redis server cache")
describe = next(t for t in step_tasks if t["name"].endswith("Describe the server step"))
step_spec = {k: v for k, v in describe["ansible.builtin.set_fact"]["k8s_database_step"].items()
             if k != "secret_data"}
step_fact = Templar(loader=DataLoader(), variables={"_kds": fact["_kds"]}).template(
    step_spec, fail_on_undefined=True)
assert step_fact["env"] == {
    "DB_ENGINE": "redis", "DB_HOST": "cache", "DB_PORT": "6380", "DB_USER": "default",
}, step_fact["env"]
assert "redis.rdb" in step_fact["dump"] and "server-load" in step_fact["load"]
client = read("ansible/tasks/database/scripts/database-client.sh")
assert "redis_server()" in client and "--rdb" in client and "REPLICAOF" in client
# The target keeps its own default login: the point's `default` user is never applied.
assert "grep -v '^user default '" in client
subprocess.run(["sh", "-n", str(ROOT / "ansible/tasks/database/scripts/database-client.sh")], check=True)


# ── Error replies fail the script: redis-cli exits 0 on them unless -e is passed ───────
import os, tempfile
with tempfile.TemporaryDirectory(prefix="redis-client-") as tmp:
    bin_dir = Path(tmp)
    # A fake server: answers ok, except the command named in FAIL_ON, which gets an error
    # reply. Like the real client it exits non-zero on that reply only when given -e.
    (bin_dir / "redis-cli").write_text(
        '#!/bin/sh\nflag=0; for a in "$@"; do [ "$a" = -e ] && flag=1; done\n'
        'case " $* " in *" $FAIL_ON "*) echo "(error) ERR injected"; [ "$flag" = 1 ] && exit 1; exit 0 ;; esac\n'
        'case " $* " in *" DBSIZE "*) echo 3 ;; *" ping "*) echo PONG ;; *" ACL LIST "*) echo "user default on nopass"; echo "user old on nopass" ;; '
        '*" ACL USERS "*) echo default ;; *" INFO "*) echo master_link_status:up ;; esac\nexit 0\n')
    (bin_dir / "redis-server").write_text("#!/bin/sh\nexec sleep 30\n")
    (bin_dir / "redis-check-rdb").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "hostname").write_text("#!/bin/sh\necho 10.42.0.9\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    point = bin_dir / "redis.rdb"
    point.write_text("rdb")
    (bin_dir / "redis.acl").write_text("user default on nopass\nuser app on nopass ~* +@all\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DB_ENGINE": "redis",
           "DB_HOST": "cache", "DB_PORT": "6379", "DB_USER": "default", "DB_PASSWORD": "x"}
    script = str(ROOT / "ansible/tasks/database/scripts/database-client.sh")

    def run(action, fail_on, *args):
        return subprocess.run(["sh", script, action, *args], env={**env, "FAIL_ON": fail_on},
                              capture_output=True, text=True, timeout=120)

    assert run("server-load", "NONE", str(point)).returncode == 0
    for command in ("REPLICAOF NO ONE", "SETUSER", "DELUSER", "SAVE"):
        result = run("server-load", command, str(point))
        assert result.returncode != 0 and "complete" not in result.stdout, (command, result)
    # A full sync that never finishes must leave the target promoted again, not replicating.
    (bin_dir / "calls").write_text("")
    cli_text = (bin_dir / "redis-cli").read_text()
    (bin_dir / "redis-cli").write_text(cli_text.replace(
        "case \" $* \" in *\" $FAIL_ON \"*)",
        "echo \"$*\" >> " + str(bin_dir / "calls") + "\ncase \" $* \" in *\" INFO \"*) [ -n \"$STALL\" ] && echo master_link_status:down && exit 0 ;; esac\n"
        "case \" $* \" in *\" $FAIL_ON \"*)", 1))
    stalled = subprocess.run(["sh", script, "server-load", str(point)],
                             env={**env, "FAIL_ON": "NONE", "STALL": "1", "SYNC_WAIT": "2"},
                             capture_output=True, text=True, timeout=300)
    assert stalled.returncode != 0
    calls = (bin_dir / "calls").read_text().splitlines()
    assert any("REPLICAOF 10.42.0.9 6390" in c for c in calls)
    assert calls[-1].endswith("REPLICAOF NO ONE"), calls[-3:]
    assert run("server-dump", "ACL LIST", str(bin_dir / "out.rdb")).returncode != 0
    assert run("server-inspect", "NONE").stdout.strip() == "3 keys, 1 ACL users"
    # A bad DBSIZE reply must not be reported as a count.
    assert run("server-inspect", "DBSIZE").returncode != 0

# ── Firewall: consumers name Redis by redis_host and redis_port ────────────────────────
access = read("ansible/tasks/database/open-consumer-access.yml")
assert "'redis' if database_consumer_provider == 'redis'" in access and "_sql_prefix ~ '_host'" in access

# ── No role-local copy of a shared operation ───────────────────────────────────────────
assert not (ROLE / "templates/kubernetes").exists()
assert not any("kubectl" in p.read_text(encoding="utf-8") and "apply" in p.read_text(encoding="utf-8")
               for p in (ROLE / "tasks").glob("*.yml") if p.name != "kubernetes.yml")
print("Redis keeps the guests as default and runs on Kubernetes through the shared operations")
