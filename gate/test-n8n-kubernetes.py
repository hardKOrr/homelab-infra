#!/usr/bin/env python3
"""Guard n8n's opt-in Kubernetes deployment and paired PBS recovery path."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml

from restore_swap_inputs import swap_inputs


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ansible/roles/n8n/templates"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def render(path, context):
    environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    return environment.from_string((TEMPLATES / path).read_text(encoding="utf-8")).render(
        **context
    )


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

# Backup and restore go through the shared Kubernetes recovery pair, which
# gate/test-k8s-recovery-jobs.py renders with n8n's inputs.
restore_tasks = yaml.safe_load(read("ansible/roles/n8n/tasks/restore-kubernetes.yml"))
include = next(task for task in restore_tasks
               if "recovery-restore.yml" in task.get("ansible.builtin.include_tasks", ""))
recovery = include["vars"]["k8s_recovery"]
assert recovery["archives"] == [
    {"name": "data", "claim": "{{ restore_target }}-storage", "owner": "1000:1000"}
], "the restored data volume must stay owned by the image's node user"
assert recovery["after_restore_tasks"].endswith("/tasks/restore-kubernetes-key.yml")
key_tasks = [task["name"] for task in yaml.safe_load(read("ansible/roles/n8n/tasks/restore-kubernetes-key.yml"))]
assert key_tasks == [
    "n8n restore | Record the source encryption key for the target",
    "n8n restore | Sync the restored key and target environment",
]
# Docker hosting restores through the shared swap with Compose (gate/test-n8n-restore.py).
assert swap_inputs("n8n", "restore-docker.yml")["recovery_swap_runtime"] == "docker"
assert "docker-compose.yml.j2" in read("ansible/roles/n8n/tasks/main.yml")

print("n8n keeps Docker as default and renders its Kubernetes workload and paired PBS restore")
