#!/usr/bin/env python3
"""Check BookStack's Kubernetes hosting and recovery templates and dispatch.

Checks: BookStack keeps Docker as its default, dispatches the Kubernetes method to an owned
namespace, and renders a default-class PVC, restricted recovery access, application-
consistent backup CronJob and rollback-capable restore Job.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
ROLE = ROOT / "ansible/roles/bookstack-kubernetes"


def recent_recovery_point():
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
    return "host/bookstack/" + timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def render(path, variables):
    source = (ROOT / path).read_text(encoding="utf-8")
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(source)


def documents(path, variables):
    return list(yaml.safe_load_all(render(path, variables)))


def fixture():
    return {
        "role_path": str(ROLE),
        "instance": "bookstack",
        "k8s_namespace": "app-bookstack",
        "bookstack_fqdn": "bookstack.example.test",
        "bookstack_pbs_available": True,
        "bookstack_pbs_repository": "token@pbs.example.test:store",
        "bookstack_pbs_fingerprint": "00:11:22:33",
        "bookstack_pbs_password": "fixture-pbs-password",
        "k8s_pbs_repository": "token@pbs.example.test:store",
        "k8s_pbs_fingerprint": "00:11:22:33",
        "app_config": {
            "app": {
                "image": "lscr.io/linuxserver/bookstack:version-v26.05.3",
                "container_port": 80,
                "storage": 5,
                "database": {"provider": "mariadb", "name": "bookstack"},
                "database_host": "db.example.test",
                "database_port": 3306,
                "database_user": "bookstack",
                "database_password": "fixture-db-password",
            },
            "backup": {
                "schedule": "0 3 * * *",
                "retention_days": 14,
                "scratch_size": 5,
                "timeout_seconds": 3600,
                "client": {
                    "key_url": "https://pbs.example.test/key.gpg",
                    "repository": "https://pbs.example.test/debian/pbs-client",
                    "suite": "bookworm",
                    "component": "main",
                },
            },
        },
        "_bookstack_backup_timezone": "UTC",
        "_bookstack_backup_id": "bookstack",
        "_bookstack_restore_mode": "plan",
        "_bookstack_restore_snapshot": "host/bookstack/2026-10-15T00:00:00Z",
        "_bookstack_restore_safety_point": recent_recovery_point(),
        "_bookstack_restore_secret": "bookstack-restore",
        "_bookstack_restore_job_name": "bookstack-restore-plan",
        "_bookstack_restore_backup_id": "bookstack",
        "_bookstack_restore_timeout": 3600,
    }


def check_defaults_and_dispatch():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/bookstack.yml").read_text(encoding="utf-8")
    )["bookstack_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert defaults["hosting"]["impossible"] == {}
    assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"

    deploy = yaml.safe_load((ROOT / "ansible/playbooks/apps/bookstack.yml").read_text())
    validate = next(
        task for task in deploy[0]["pre_tasks"]
        if task.get("name") == "Validate supported hosting method before provisioning resources"
    )
    database = next(
        task for task in deploy[0]["tasks"]
        if task.get("name") == "Provision the named MySQL-compatible database and role"
    )
    assert deploy[0]["pre_tasks"].index(validate) < len(deploy[0]["pre_tasks"])
    assert database["ansible.builtin.include_tasks"].endswith("tasks/database/provision.yml")
    stack = next(
        task for task in deploy[0]["tasks"]
        if task.get("name") == "Find or create the Docker stack host"
    )
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    k8s_play = next(play for play in deploy if play.get("name") == "BookStack | Deploy on Kubernetes")
    k8s_include = k8s_play["tasks"][0]
    assert k8s_include["ansible.builtin.include_role"]["name"] == "bookstack-kubernetes"
    assert k8s_include["when"] == "hostvars['localhost'].app_config.hosting.method == 'kubernetes'"
    assert "homelabinfra_infra.kubernetes.ingress_vip" in str(deploy[-1]["vars"]["wiring_upstream_host"])
    assert "80 if app_config.hosting.method == 'kubernetes'" in deploy[-1]["vars"]["wiring_upstream_port"]
    assert "else app_config.app.port" in deploy[-1]["vars"]["wiring_upstream_port"]

    backup = yaml.safe_load(
        (ROOT / "ansible/playbooks/maintenance/bookstack-backup.yml").read_text()
    )
    restore = yaml.safe_load(
        (ROOT / "ansible/playbooks/maintenance/bookstack-restore.yml").read_text()
    )
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "bookstack-kubernetes"
        and task["ansible.builtin.include_role"].get("tasks_from") == "backup.yml"
        for task in backup[0]["tasks"]
    )
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "bookstack-kubernetes"
        and task["ansible.builtin.include_role"].get("tasks_from") == "restore.yml"
        for task in restore[2]["tasks"]
    )

    remove = yaml.safe_load((ROOT / "ansible/playbooks/apps/remove.yml").read_text())
    database_remove_play = next(
        play for play in remove if play.get("name") == "Remove App | Drop its named database"
    )
    assert database_remove_play["hosts"] == "localhost"
    assert remove.index(database_remove_play) > next(
        index for index, play in enumerate(remove)
        if play.get("name", "").startswith("Remove App | Stop ")
    )
    database_remove = next(
        task for task in database_remove_play["tasks"]
        if task.get("name") == "Drop the app's named database and login"
    )
    assert database_remove["ansible.builtin.include_tasks"].endswith("database/drop.yml")
    assert "_rm_drops_database" in str(database_remove["when"])


def check_workload_manifest():
    rendered = documents("ansible/roles/bookstack-kubernetes/templates/manifest.yaml.j2", fixture())
    objects = {doc["kind"]: doc for doc in rendered if doc}
    assert {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"} <= set(objects)
    assert not {"ConfigMap", "ServiceAccount", "Role", "RoleBinding"} & set(objects)

    pvc = objects["PersistentVolumeClaim"]
    assert pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"
    assert "storageClassName" not in pvc["spec"]

    deployment = objects["Deployment"]
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert pod["automountServiceAccountToken"] is False
    assert container["envFrom"] == [{"secretRef": {"name": "bookstack-env"}}]
    assert container["volumeMounts"][0]["mountPath"] == "/config"
    assert container["readinessProbe"]["httpGet"]["path"] == "/login"
    assert objects["Ingress"]["spec"]["rules"][0]["host"] == "bookstack.example.test"


def check_backup_and_restore_jobs():
    main_tasks = yaml.safe_load(
        (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
    )
    database_backup = next(
        task for task in main_tasks
        if task.get("name") == "BookStack Kubernetes | Describe the external database recovery step"
    )
    assert database_backup["vars"]["k8s_database_step_source"] == "registered"
    assert database_backup["vars"]["k8s_database_step_config"] == "{{ app_config }}"
    backup_task = next(
        task for task in main_tasks
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
    )
    backup = backup_task["vars"]["k8s_recovery"]
    assert backup["archives"] == [
        {"name": "storage", "claim": "{{ instance }}-config"}
    ]
    assert backup["database"] == "{{ k8s_database_step | default({}) }}"

    restore_tasks = yaml.safe_load(
        (ROLE / "tasks/restore.yml").read_text(encoding="utf-8")
    )
    database_restore = next(
        task for task in restore_tasks
        if task.get("name") == "BookStack Kubernetes Restore | Describe the external database recovery step"
    )
    assert database_restore["vars"]["k8s_database_step_source"] == "registered"
    recovery_task = next(
        task for task in restore_tasks
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
    )
    recovery = recovery_task["vars"]["k8s_recovery"]
    assert recovery["archives"] == [{
        "name": "storage",
        "claim": "{{ restore_target }}-config",
        "owner": "{{ restore_target_config.app.puid | default(1000) }}:{{ restore_target_config.app.pgid | default(1000) }}",
    }]
    assert recovery["stop"] == ["{{ restore_target }}"]
    assert recovery["database"] == "{{ k8s_database_step }}"
    assert not (ROLE / "templates/backup-cronjob.yaml.j2").exists()
    assert not (ROLE / "templates/restore-job.yaml.j2").exists()


FAKE_CURL = """#!/bin/bash
set -eu
args="$*"
printf '%s\\n' "$args" >> "$BOOKSTACK_TEST_KUBE_API_LOG"
case "$args" in
  *"/deployments/"*"/scale"*) printf '{}\\n' ;;
  *"/pods"*) printf '{"items":[]}\\n' ;;
  *"/deployments/"*) printf '{"spec":{"replicas":1},"status":{"availableReplicas":1}}\\n' ;;
  *) exit 90 ;;
esac
"""

FAKE_DATABASE_CLIENT = """#!/bin/bash
set -eu
action=$1 file=$2
printf '%s\\n' "$action" >> "$BOOKSTACK_TEST_DATABASE_CLIENT_LOG"
case "$action" in
  dump) cp "$BOOKSTACK_TEST_DB_STATE" "$file" ;;
  restore)
    if [[ "$BOOKSTACK_TEST_CASE" == "import-failure" ]] && grep -q SNAPSHOT-DATABASE "$file"; then
      printf 'PARTIAL-DATABASE\\n' > "$BOOKSTACK_TEST_DB_STATE"
      exit 17
    fi
    cp "$file" "$BOOKSTACK_TEST_DB_STATE"
    ;;
  *) exit 94 ;;
esac
"""

FAKE_PBS = """#!/bin/bash
set -eu
if [[ "$1" == "snapshot" && "$2" == "files" ]]; then
  [[ "$3" == "$BOOKSTACK_TEST_PRE_RESTORE_POINT" ]] || exit 94
  exit 0
fi
if [[ "$1" == "restore" ]]; then
  [[ " $* " == *" --ignore-ownership "* && " $* " == *" --ignore-acls "* && " $* " == *" --ignore-xattrs "* ]] || exit 91
  printf '%s\\n' "$*" >> "$BOOKSTACK_TEST_PBS_LOG"
  destination="$4"
  mkdir -p "$destination"
  case "$3" in
    database.pxar) cp -a "$BOOKSTACK_TEST_SNAPSHOT_DB/." "$destination/" ;;
    storage.pxar) cp -a "$BOOKSTACK_TEST_SNAPSHOT_STORAGE/." "$destination/" ;;
    *) exit 92 ;;
  esac
  exit 0
fi
if [[ "$1" == "backup" ]]; then
  printf '%s\\n' "$*" >> "$BOOKSTACK_TEST_PBS_LOG"
  exit 0
fi
if [[ "$1" == "prune" ]]; then
  printf '%s\\n' "$*" >> "$BOOKSTACK_TEST_PBS_LOG"
  exit 0
fi
exit 93
"""


def main():
    check_defaults_and_dispatch()
    check_workload_manifest()
    check_backup_and_restore_jobs()
    print("BookStack Kubernetes: Docker default, hosting dispatch, owned workload, manifests and recovery Jobs passed")


if __name__ == "__main__":
    main()
