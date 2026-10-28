#!/usr/bin/env python3
"""Check BookStack's Kubernetes hosting and recovery templates and dispatch."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import tempfile

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
        "_bookstack_recovery_script": (
            ROOT / "ansible/roles/bookstack/files/bookstack-recovery"
        ).read_text(encoding="utf-8"),
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
    bookstack_remove_play = next(
        play for play in remove if play.get("name") == "Remove BookStack | Delete its named database"
    )
    assert bookstack_remove_play["hosts"] == "localhost"
    assert remove.index(bookstack_remove_play) > next(
        index for index, play in enumerate(remove)
        if play.get("name", "").startswith("Remove App | Stop ")
    )
    bookstack_remove = next(
        task for task in bookstack_remove_play["tasks"]
        if task.get("name") == "Remove BookStack's named database and role"
    )
    assert "delete_data" in str(bookstack_remove["when"])


def check_workload_manifest():
    rendered = documents("ansible/roles/bookstack-kubernetes/templates/manifest.yaml.j2", fixture())
    objects = {doc["kind"]: doc for doc in rendered if doc}
    assert {"ConfigMap", "PersistentVolumeClaim", "ServiceAccount", "Role", "RoleBinding",
            "Deployment", "Service", "Ingress"} <= set(objects)

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
    assert "bookstack-recovery" in objects["ConfigMap"]["data"]
    assert "kube_scale_bookstack 0" in objects["ConfigMap"]["data"]["bookstack-recovery"]

    role_rules = objects["Role"]["rules"]
    scale_rule = next(rule for rule in role_rules if "deployments/scale" in rule["resources"])
    assert scale_rule["resourceNames"] == ["bookstack"]
    assert scale_rule["verbs"] == ["get", "patch"]
    pod_rule = next(rule for rule in role_rules if "pods" in rule["resources"])
    assert set(pod_rule["verbs"]) == {"get", "list", "watch"}


def check_backup_and_restore_jobs():
    context = fixture()
    restore_tasks = yaml.safe_load(
        (ROOT / "ansible/roles/bookstack-kubernetes/tasks/restore.yml").read_text()
    )
    render_task = next(
        task for task in restore_tasks
        if task.get("name") == "BookStack Kubernetes Restore | Render the recovery Job"
    )
    manifest_expression = render_task["ansible.builtin.set_fact"]["_bookstack_restore_manifest"]
    assert "convert_data=False" in manifest_expression

    backup = documents("ansible/roles/bookstack-kubernetes/templates/backup-cronjob.yaml.j2", context)[0]
    spec = backup["spec"]
    pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert isinstance(container["args"], list) and all(isinstance(arg, str) for arg in container["args"])
    assert spec["schedule"] == "0 3 * * *"
    assert spec["concurrencyPolicy"] == "Forbid"
    assert pod["serviceAccountName"] == "bookstack-recovery"
    assert "bookstack-recovery --backup" in container["args"][0]
    env = {item["name"]: item for item in container["env"]}
    assert env["BOOKSTACK_DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "db-password"
    assert env["PBS_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "pbs-password"
    mounts = {item["name"]: item for item in container["volumeMounts"]}
    assert mounts["config"]["mountPath"] == "/config" and mounts["config"]["readOnly"] is True
    assert pod["volumes"][-2]["persistentVolumeClaim"]["claimName"] == "bookstack-config"

    plan = documents("ansible/roles/bookstack-kubernetes/templates/restore-job.yaml.j2", context)[0]
    plan_pod = plan["spec"]["template"]["spec"]
    plan_container = plan_pod["containers"][0]
    assert isinstance(plan_container["args"], list) and all(
        isinstance(arg, str) for arg in plan_container["args"]
    ), "restore Job args must be a list of strings"
    assert plan_container["env"][0]["name"] == "BOOKSTACK_BACKEND"
    assert plan_pod["serviceAccountName"] == "bookstack-recovery"
    assert "--plan --snapshot 'host/bookstack/2026-10-15T00:00:00Z'" in plan_container["args"][0]
    assert all(volume["name"] != "config" for volume in plan_pod["volumes"])

    context.update(
        {
            "_bookstack_restore_mode": "restore",
            "_bookstack_restore_job_name": "bookstack-restore-run",
        }
    )
    overwrite = documents(
        "ansible/roles/bookstack-kubernetes/templates/restore-job.yaml.j2", context
    )[0]
    run_pod = overwrite["spec"]["template"]["spec"]
    run_container = run_pod["containers"][0]
    assert isinstance(run_container["args"], list) and all(
        isinstance(arg, str) for arg in run_container["args"]
    )
    assert "--restore --snapshot 'host/bookstack/2026-10-15T00:00:00Z'" in run_container["args"][0]
    assert (
        f"--pre-restore-point '{context['_bookstack_restore_safety_point']}'"
        in run_container["args"][0]
    )
    run_mounts = {item["name"]: item for item in run_container["volumeMounts"]}
    assert run_mounts["client-cache"]["mountPath"] == "/root/.cache"
    run_volumes = {item["name"]: item for item in run_pod["volumes"]}
    assert run_volumes["client-cache"]["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "64Mi",
    }
    assert "client-cache" not in {item["name"] for item in plan_container["volumeMounts"]}
    assert any(volume.get("persistentVolumeClaim", {}).get("claimName") == "bookstack-config"
               for volume in run_pod["volumes"])


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

FAKE_MARIADB_DUMP = """#!/bin/bash
set -eu
cat "$BOOKSTACK_TEST_DB_STATE"
"""

FAKE_MARIADB = """#!/bin/bash
set -eu
all_args="$*"
if [[ "$all_args" == *"SELECT CONCAT"* ]]; then
  current=$(cat "$BOOKSTACK_TEST_DB_STATE")
  if [[ -n "$current" && "$current" != "EMPTY" ]]; then
    printf '%s\\n' 'DROP TABLE IF EXISTS `references`;'
  fi
  exit 0
fi
input=$(cat)
if [[ "$input" == *"DROP TABLE IF EXISTS"* ]]; then
  printf 'EMPTY\\n' > "$BOOKSTACK_TEST_DB_STATE"
  exit 0
fi
if [[ "$BOOKSTACK_TEST_CASE" == "import-failure" && "$input" == *"SNAPSHOT-DATABASE"* ]]; then
  printf 'PARTIAL-DATABASE\\n' > "$BOOKSTACK_TEST_DB_STATE"
  exit 17
fi
printf '%s\\n' "$input" > "$BOOKSTACK_TEST_DB_STATE"
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


def write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def kubernetes_recovery_environment(root, case="success"):
    bin_dir = root / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "curl", FAKE_CURL)
    write_executable(bin_dir / "mariadb", FAKE_MARIADB)
    write_executable(bin_dir / "mariadb-dump", FAKE_MARIADB_DUMP)
    write_executable(bin_dir / "proxmox-backup-client", FAKE_PBS)

    data = root / "pvc" / "config"
    data.mkdir(parents=True)
    data.chmod(0o750)
    (data / "canary.txt").write_text("original canary\n", encoding="utf-8")
    (data / ".original-hidden").write_text("original hidden\n", encoding="utf-8")
    database = root / "database-state.sql"
    database.write_text("ORIGINAL-DATABASE\n", encoding="utf-8")
    snapshot_database = root / "snapshot-database"
    snapshot_database.mkdir()
    (snapshot_database / "database.sql").write_text("SNAPSHOT-DATABASE\n", encoding="utf-8")
    snapshot_storage = root / "snapshot-storage"
    snapshot_storage.mkdir()
    (snapshot_storage / "restored.txt").write_text("restored canary\n", encoding="utf-8")
    (snapshot_storage / ".restored-hidden").write_text("restored hidden\n", encoding="utf-8")

    token = root / "service-account-token"
    token.write_text("test-token\n", encoding="utf-8")
    ca = root / "service-account-ca"
    ca.write_text("test-ca\n", encoding="utf-8")
    now_point = recent_recovery_point()
    environment = os.environ | {
        "BOOKSTACK_BACKEND": "kubernetes",
        "BOOKSTACK_INSTANCE": "bookstack",
        "BOOKSTACK_SERVICE": "bookstack",
        "BOOKSTACK_NAMESPACE": "app-bookstack",
        "BOOKSTACK_DATA_PATH": str(data),
        "BOOKSTACK_DB_PROVIDER": "mariadb",
        "BOOKSTACK_DB_HOST": "db.example.test",
        "BOOKSTACK_DB_PORT": "3306",
        "BOOKSTACK_DB_NAME": "bookstack",
        "BOOKSTACK_DB_USER": "bookstack",
        "BOOKSTACK_DB_PASSWORD": "fixture-db-password",
        "PBS_REPOSITORY": "fixture@pbs.example.test:store",
        "PBS_FINGERPRINT": "fixture-fingerprint",
        "PBS_PASSWORD": "fixture-pbs-password",
        "PBS_BACKUP_ID": "bookstack",
        "PBS_RETENTION_DAYS": "14",
        "KUBERNETES_SERVICE_HOST": "10.96.0.1",
        "KUBERNETES_SERVICE_PORT_HTTPS": "443",
        "BOOKSTACK_K8S_TOKEN_FILE": str(token),
        "BOOKSTACK_K8S_CA_FILE": str(ca),
        "BOOKSTACK_TEST_CASE": case,
        "BOOKSTACK_TEST_DB_STATE": str(database),
        "BOOKSTACK_TEST_SNAPSHOT_DB": str(snapshot_database),
        "BOOKSTACK_TEST_SNAPSHOT_STORAGE": str(snapshot_storage),
        "BOOKSTACK_TEST_PRE_RESTORE_POINT": now_point,
        "BOOKSTACK_TEST_KUBE_API_LOG": str(root / "kube-api.log"),
        "BOOKSTACK_TEST_PBS_LOG": str(root / "pbs.log"),
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
    }
    return environment, data, database, root / "kube-api.log", root / "pbs.log", now_point


def check_kubernetes_backup():
    with tempfile.TemporaryDirectory(prefix="bookstack-k8s-backup-test-") as temporary:
        root = Path(temporary)
        environment, _, _, api_log, pbs_log, _ = kubernetes_recovery_environment(root)
        result = subprocess.run(
            [str(ROOT / "ansible/roles/bookstack/files/bookstack-recovery"), "--backup"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        api = api_log.read_text(encoding="utf-8")
        pbs = pbs_log.read_text(encoding="utf-8")
        assert '"replicas":0' in api and '"replicas":1' in api, api
        assert "--data-urlencode labelSelector=" in api and "/deployments/bookstack" in api, api
        assert "database.pxar:" in pbs and "storage.pxar:" in pbs
        assert "--backup-id bookstack" in pbs and "--keep-last 3" in pbs
        assert "BookStack backup complete" in result.stdout


def check_kubernetes_restore(case):
    with tempfile.TemporaryDirectory(prefix="bookstack-k8s-restore-test-") as temporary:
        root = Path(temporary)
        environment, data, database, api_log, _, safety_point = kubernetes_recovery_environment(root, case)
        original_data_stat = data.stat()
        result = subprocess.run(
            [str(ROOT / "ansible/roles/bookstack/files/bookstack-recovery"),
             "--restore", "--snapshot", "host/bookstack/2026-10-27T07:30:00Z",
             "--pre-restore-point", safety_point],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        api = api_log.read_text(encoding="utf-8") if api_log.exists() else ""
        if case == "success":
            assert result.returncode == 0, result.stdout + result.stderr
            assert (data / "restored.txt").read_text(encoding="utf-8") == "restored canary\n"
            assert (data / ".restored-hidden").read_text(encoding="utf-8") == "restored hidden\n"
            assert not (data / "canary.txt").exists()
            assert not (data / ".original-hidden").exists()
            assert database.read_text(encoding="utf-8") == "SNAPSHOT-DATABASE\n"
            assert api.count('"replicas":0') == 1 and '"replicas":1' in api, api
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert "BookStack rollback restored the previous data and database." in result.stderr
            assert (data / "canary.txt").read_text(encoding="utf-8") == "original canary\n"
            assert (data / ".original-hidden").read_text(encoding="utf-8") == "original hidden\n"
            assert not (data / "restored.txt").exists()
            assert not (data / ".restored-hidden").exists()
            assert database.read_text(encoding="utf-8") == "ORIGINAL-DATABASE\n"
            assert api.count('"replicas":0') == 2 and '"replicas":1' in api, api
        final_data_stat = data.stat()
        assert final_data_stat.st_ino == original_data_stat.st_ino
        assert final_data_stat.st_uid == original_data_stat.st_uid
        assert final_data_stat.st_gid == original_data_stat.st_gid
        assert final_data_stat.st_mode == original_data_stat.st_mode
        assert not list(data.glob(".pre-restore.*"))


def check_kubernetes_recovery():
    check_kubernetes_backup()
    check_kubernetes_restore("success")
    check_kubernetes_restore("import-failure")


def main():
    check_defaults_and_dispatch()
    check_workload_manifest()
    check_backup_and_restore_jobs()
    check_kubernetes_recovery()
    print("BookStack Kubernetes: Docker default, hosting dispatch, owned workload, manifests and recovery rollback passed")


if __name__ == "__main__":
    main()
