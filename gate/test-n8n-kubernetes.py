#!/usr/bin/env python3
"""Guard n8n's opt-in Kubernetes deployment and paired PBS recovery path."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ansible/roles/n8n/templates"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(path, context):
    environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    return environment.from_string((TEMPLATES / path).read_text(encoding="utf-8")).render(
        **context
    )


def flatten_tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from flatten_tasks(task.get(key, []))


defaults = yaml.safe_load(read("ansible/vars/app-defaults/n8n.yml"))["n8n_defaults"]
assert defaults["hosting"]["method"] == "docker"
assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
assert defaults["hosting"]["impossible"] == {}
assert defaults["app"]["storage"] > 0
assert defaults["firewall"]["egress"][0]["port"] == (
    "{{ app_config.app.database_port | default(5432) }}"
)

playbook = yaml.safe_load(read("ansible/playbooks/apps/n8n.yml"))
provision_tasks = playbook[0]["tasks"]
assert any(
    task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
    and task.get("when") == "app_config.hosting.method == 'kubernetes'"
    for task in provision_tasks
), "n8n Deploy must call the Kubernetes role when selected"
assert any(
    task.get("ansible.builtin.import_tasks")
    == "../../tasks/stack/find-or-create-host.yml"
    and task.get("when") == "app_config.hosting.method == 'docker'"
    for task in provision_tasks
), "Docker remains the default guest path"
assert "tasks/kubernetes/open-app-access.yml" in read("ansible/playbooks/apps/n8n.yml")
assert "homelabinfra_infra.kubernetes.ingress_vip" in read(
    "ansible/playbooks/apps/n8n.yml"
)

context = {
    "instance": "n8n-test",
    "n8n_fqdn": "n8n-test.example.test",
    "k8s_pbs_fingerprint": "AA:" * 31 + "AA",
    "_n8n_backup_id": "n8n-test",
    "_n8n_backup_timezone": "UTC",
    "_n8n_backup_repository": "backup@pbs!token@pbs.example.test:store",
    "_n8n_pg_client_version": "16",
    "_n8n_k8s_r_mode": "plan",
    "_n8n_k8s_r_target": "n8n-test",
    "_n8n_k8s_r_job_name": "n8n-test-restore-plan",
    "_n8n_k8s_r_timeout": 900,
    "_n8n_k8s_r_secret": "n8n-test-restore",
    "_n8n_k8s_r_snapshot": "host/n8n-test/2026-10-26T12:00:00Z",
    "_n8n_k8s_r_backup_id": "n8n-test",
    "_n8n_k8s_r_database_host": "postgres.example.test",
    "_n8n_k8s_r_database_port": 5432,
    "_n8n_k8s_r_database_name": "n8n",
    "_n8n_k8s_r_database_user": "n8n",
    "_n8n_k8s_r_pg_client_version": "16",
    "k8s_pbs_repository": "backup@pbs!token@pbs.example.test:store",
    "k8s_pbs_fingerprint": "AA:" * 31 + "AA",
    "app_config": {
        "app": {
            "image": "docker.n8n.io/n8nio/n8n:1.104.2",
            "port": 5678,
            "storage": 5,
            "database": {"name": "n8n"},
            "database_host": "postgres.example.test",
            "database_port": 5432,
            "database_user": "n8n",
        },
        "backup": {
            "schedule": "0 3 * * *",
            "timeout_seconds": 3600,
            "retention_days": 14,
            "scratch_size": 2,
            "client": {
                "image": "buildpack-deps:bookworm-curl",
                "key_url": "https://pbs.example.test/proxmox-release.gpg",
                "repository": "http://pbs.example.test/debian/pbs-client",
                "suite": "bookworm",
                "component": "main",
            },
        },
    },
}

workload = list(yaml.safe_load_all(render("manifest.yaml.j2", context)))
pvc = next(item for item in workload if item["kind"] == "PersistentVolumeClaim")
deployment = next(item for item in workload if item["kind"] == "Deployment")
service = next(item for item in workload if item["kind"] == "Service")
ingress = next(item for item in workload if item["kind"] == "Ingress")
assert "storageClassName" not in pvc["spec"]
assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"
pod = deployment["spec"]["template"]["spec"]
container = pod["containers"][0]
assert pod["securityContext"]["runAsUser"] == 1000
assert container["envFrom"][0]["secretRef"]["name"] == "n8n-test-env"
assert container["volumeMounts"][0]["mountPath"] == "/home/node/.n8n"
assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
assert service["spec"]["ports"][0]["targetPort"] == 5678
assert ingress["spec"]["rules"][0]["host"] == "n8n-test.example.test"

backup_documents = list(yaml.safe_load_all(render("backup-cronjob.yaml.j2", context)))
trust = next(item for item in backup_documents if item["kind"] == "ConfigMap")
cronjob = next(item for item in backup_documents if item["kind"] == "CronJob")
assert trust["metadata"]["labels"]["homelab-infra.io/pbs-fingerprint"] == "managed"
assert trust["data"]["fingerprint"] == "AA:" * 31 + "AA"
assert cronjob["metadata"]["labels"]["homelab-infra.io/pbs-fingerprint"] == "managed"
backup_pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
backup_container = backup_pod["containers"][0]
backup_env = {item["name"]: item for item in backup_container["env"]}
assert backup_env["PBS_FINGERPRINT"]["valueFrom"]["configMapKeyRef"]["name"] == "pbs-backup-trust"
assert backup_env["PBS_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "n8n-test-backup"
assert backup_env["PGPASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "db-password"
assert "database.pxar:/backup/database data.pxar:/app-data" in backup_container["args"][0]
backup_volume = next(item for item in backup_pod["volumes"] if item["name"] == "app-data")
assert backup_volume["persistentVolumeClaim"]["claimName"] == "n8n-test-storage"
assert backup_volume["persistentVolumeClaim"]["readOnly"] is True

plan_job = yaml.safe_load(render("restore-job.yaml.j2", context))
plan_pod = plan_job["spec"]["template"]["spec"]
plan_container = plan_pod["containers"][0]
plan_volume = next(item for item in plan_pod["volumes"] if item["name"] == "app-data")
assert plan_volume["persistentVolumeClaim"]["readOnly"] is True
assert "snapshot list" in plan_container["args"][0]
context["_n8n_k8s_r_mode"] = "restore"
restore_job = yaml.safe_load(render("restore-job.yaml.j2", context))
restore_pod = restore_job["spec"]["template"]["spec"]
restore_container = restore_pod["containers"][0]
restore_script = restore_container["args"][0]
restore_volume = next(item for item in restore_pod["volumes"] if item["name"] == "app-data")
assert restore_volume["persistentVolumeClaim"].get("readOnly", False) is False
assert 'restore "${SNAPSHOT}" database.pxar /restore/database' in restore_script
assert 'restore "${SNAPSHOT}" data.pxar /restore/data' in restore_script
assert restore_script.index("pg_restore --clean") < restore_script.index(
    "find /app-data -mindepth 1"
)
assert restore_script.index("find /app-data -mindepth 1") < restore_script.index(
    "cp -a /restore/data/. /app-data/"
)

restore_tasks = yaml.safe_load(read("ansible/roles/n8n/tasks/restore-kubernetes.yml"))
task_names = [task.get("name", "") for task in flatten_tasks(restore_tasks)]
assert task_names.index("n8n restore | Stop the target Deployment") < task_names.index(
    "n8n restore | Run the restore Job"
)
assert task_names.index("n8n restore | Record the source encryption key for the target") < task_names.index(
    "n8n restore | Start the target Deployment"
)
assert any("failed restore" in name for name in task_names)
docker_restore = read("ansible/roles/n8n/tasks/restore-docker.yml")
assert "community.docker.docker_compose_v2" in docker_restore
assert "docker-compose.yml.j2" in read("ansible/roles/n8n/tasks/main.yml")

print("n8n keeps Docker as default and renders its Kubernetes workload and paired PBS restore")
