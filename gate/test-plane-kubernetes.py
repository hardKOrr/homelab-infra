#!/usr/bin/env python3
"""Check Plane's opt-in Kubernetes workload and shared recovery contract."""

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from restore_swap_inputs import swap_inputs


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/plane"
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")


def render(path, variables):
    source = (ROOT / path).read_text(encoding="utf-8")
    return Templar(loader=DataLoader(), variables=variables).template(source)


def render_expression(expression, variables):
    return Templar(loader=DataLoader(), variables=variables).template(expression)


def documents(path, variables):
    return [doc for doc in yaml.safe_load_all(render(path, variables)) if doc]


def fixture():
    return {
        "instance": "plane",
        "plane_fqdn": "plane.example.test",
        "app_config": {
            "app": {
                "release": "v1.4.2",
                "port": 8093,
                "data_storage": 10,
                "object_storage_storage": 20,
                "redis_recovery_storage": 2,
                "rabbitmq": {"port": 5672, "user": "plane", "vhost": "plane"},
                "object_storage": {"bucket": "plane"},
            }
        },
    }


def load_tasks(path):
    return yaml.safe_load((ROLE / path).read_text(encoding="utf-8"))


def test_runtime_environment_include():
    """Execute the actual nested include with the Deploy caller's lazy instance input."""
    tasks = load_tasks("tasks/kubernetes-env.yml")
    sync_include = next(
        task for task in tasks
        if task.get("ansible.builtin.include_tasks", "").endswith("sync-secret.yml")
    )
    app_config = {
        "app": {
            "release": "v1.4.2",
            "database_user": "plane",
            "database_password": "db-test-password",
            "database_host": "db.example.test",
            "database_port": 5432,
            "database": {"name": "plane"},
            "rabbitmq": {"user": "plane", "port": 5672, "vhost": "plane"},
            "object_storage": {"bucket": "plane"},
        }
    }
    with tempfile.TemporaryDirectory(prefix="plane-k8s-env-") as directory:
        temporary = Path(directory)
        stub_path = temporary / "sync-secret-stub.yml"
        stub_path.write_text(yaml.safe_dump([
            {
                "name": "Assert the nested sync receives the concrete target and env",
                "ansible.builtin.assert": {
                    "that": [
                        "instance == 'plane'",
                        "k8s_secret_name == 'plane-env'",
                        "k8s_secret_data.APP_RELEASE == 'v1.4.2'",
                        "k8s_secret_data.DATABASE_URL == 'postgresql://plane:db-test-password@db.example.test:5432/plane'",
                        "k8s_secret_data.REDIS_URL == 'redis://:redis-test-password@redis.example.test:6379/0'",
                        "k8s_secret_data.AMQP_URL == 'amqp://plane:mq-test-password@plane-mq:5672/plane'",
                        "k8s_secret_data.AWS_S3_ENDPOINT_URL == 'http://plane-minio:9000'",
                    ]
                },
            }
        ]), encoding="utf-8")
        sync_include["ansible.builtin.include_tasks"] = str(stub_path)
        env_tasks_path = temporary / "kubernetes-env.yml"
        env_tasks_path.write_text(yaml.safe_dump(tasks), encoding="utf-8")
        playbook_path = temporary / "env-regression.yml"
        playbook_path.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "instance": "plane",
                "_plane_redis_backend": {"host": "redis.example.test", "port": 6379},
                "_plane_redis_password": "redis-test-password",
                "_plane_secret_key": "secret-test-key",
                "_plane_live_server_secret_key": "live-test-key",
                "_plane_rabbitmq_password": "mq-test-password",
                "_plane_object_storage_access_key": "object-test-user",
                "_plane_object_storage_secret_key": "object-test-password",
            },
            "tasks": [{
                "ansible.builtin.include_tasks": str(env_tasks_path),
                "vars": {
                    "plane_k8s_env_instance": "{{ instance }}",
                    "plane_k8s_env_fqdn": "plane.example.test",
                    "plane_k8s_env_config": app_config,
                },
            }],
        }]), encoding="utf-8")
        result = subprocess.run(
            [str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
             "-i", "localhost,", "-c", "local", str(playbook_path)],
            env=os.environ | {"ANSIBLE_CONFIG": str(ROOT / "ansible/ansible.cfg"),
                              "ANSIBLE_NOCOLOR": "1"},
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_defaults_and_dispatch():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/plane.yml").read_text(encoding="utf-8")
    )["plane_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert defaults["hosting"]["impossible"] == {}
    assert defaults["backup"]["retention_days"] == 14
    assert defaults["backup"]["client"]["image"] == "python:3.12-bookworm"
    assert defaults["backup"]["redis_client_image"] == "redis:7-alpine"
    assert {rule["name"] for rule in defaults["firewall"]["egress"]} == {
        "postgresql", "redis"
    }

    main = load_tasks("tasks/main.yml")
    assert any(
        task.get("ansible.builtin.include_tasks") == "docker.yml"
        and task.get("when") == "app_config.hosting.method | default('docker') == 'docker'"
        for task in main
    )
    assert any(
        task.get("ansible.builtin.include_tasks") == "kubernetes.yml"
        and task.get("when") == "app_config.hosting.method | default('docker') == 'kubernetes'"
        for task in main
    )
    restore = load_tasks("tasks/restore.yml")
    assert any(task.get("ansible.builtin.include_tasks") == "restore-docker.yml" for task in restore)
    assert any(task.get("ansible.builtin.include_tasks") == "restore-kubernetes.yml" for task in restore)

    docker = load_tasks("tasks/docker.yml")
    assert any("docker_compose_v2" in str(task) for task in docker)
    assert (ROLE / "files/plane-recovery").is_file()


def test_workload_manifest():
    rendered = documents("ansible/roles/plane/templates/manifest.yaml.j2", fixture())
    objects = {(doc["kind"], doc["metadata"]["name"]): doc for doc in rendered}
    assert ("PersistentVolumeClaim", "plane-data") in objects
    assert ("PersistentVolumeClaim", "plane-object-storage") in objects
    assert ("PersistentVolumeClaim", "plane-redis-recovery") in objects
    assert objects[("PersistentVolumeClaim", "plane-data")]["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert objects[("PersistentVolumeClaim", "plane-object-storage")]["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert all("storageClassName" not in objects[("PersistentVolumeClaim", name)]["spec"]
               for name in ("plane-data", "plane-object-storage", "plane-redis-recovery"))

    deployments = {
        name.removeprefix("plane-")
        for kind, name in objects
        if kind == "Deployment" and name.startswith("plane-")
    }
    assert {
        "web", "space", "admin", "api", "worker", "beat", "live", "proxy", "mq", "minio"
    } <= deployments
    for component in ("web", "api", "worker", "beat", "live"):
        assert ("Deployment", f"plane-{component}") in objects

    api = objects[("Deployment", "plane-api")]["spec"]["template"]["spec"]
    assert api["containers"][0]["command"] == ["./bin/docker-entrypoint-api.sh"]
    assert api["containers"][0]["volumeMounts"][-1]["subPath"] == "api"
    assert api["volumes"][0]["persistentVolumeClaim"]["claimName"] == "plane-data"

    worker = objects[("Deployment", "plane-worker")]["spec"]["template"]["spec"]["containers"][0]
    beat = objects[("Deployment", "plane-beat")]["spec"]["template"]["spec"]["containers"][0]
    assert worker["command"] == ["./bin/docker-entrypoint-worker.sh"]
    assert worker["volumeMounts"][-1]["subPath"] == "worker"
    assert beat["command"] == ["./bin/docker-entrypoint-beat.sh"]
    assert beat["volumeMounts"][-1]["subPath"] == "beat"

    minio = objects[("Deployment", "plane-minio")]["spec"]["template"]["spec"]
    assert minio["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["labelSelector"]["matchLabels"]["app.kubernetes.io/component"] == "api"
    assert "command" not in minio["containers"][0]
    assert minio["containers"][0]["args"] == ["server", "/export", "--console-address", ":9090"]
    assert minio["volumes"][0]["persistentVolumeClaim"]["claimName"] == "plane-object-storage"

    ingress = objects[("Ingress", "plane")]
    assert ingress["spec"]["rules"][0]["host"] == "plane.example.test"
    assert ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "plane-proxy"
    assert not any(doc["kind"] == "Secret" for doc in rendered)
    assert all("namespace" not in doc["metadata"] for doc in rendered)


def test_shared_recovery_contract():
    deploy = load_tasks("tasks/kubernetes.yml")
    db_step = next(
        task for task in deploy
        if task.get("ansible.builtin.include_tasks") == "kubernetes-database-step.yml"
    )
    assert db_step["vars"]["plane_k8s_database_config"] == "{{ app_config }}"

    backup = next(
        task for task in deploy
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-backup.yml")
    )
    recovery = backup["vars"]["k8s_recovery"]
    assert [archive["name"] for archive in recovery["archives"]] == [
        "redis", "data", "object-storage"
    ]
    redis_archive = recovery["archives"][0]
    assert redis_archive["name"] == "redis"
    assert redis_archive["claim"] == "{{ instance }}-redis-recovery"
    assert redis_archive["dump_writable"] == "{{ app_config.backup.enabled | default(false) | bool }}"
    for enabled, expected in ((True, True), (False, False)):
        rendered = render_expression(
            redis_archive["dump_writable"],
            {"app_config": {"backup": {"enabled": enabled}}},
        )
        assert rendered is expected
    assert recovery["database"] == "{{ _plane_k8s_database_step | default({}) }}"
    assert set(recovery["stop"]) == {
        "{{ instance }}-web", "{{ instance }}-space", "{{ instance }}-admin",
        "{{ instance }}-api", "{{ instance }}-worker", "{{ instance }}-beat",
        "{{ instance }}-live", "{{ instance }}-proxy", "{{ instance }}-mq",
        "{{ instance }}-minio",
    }

    wrapped = load_tasks("tasks/kubernetes-database-step.yml")
    shared = next(
        task for task in wrapped
        if str(task.get("ansible.builtin.include_tasks", "")).endswith(
            "database/kubernetes-recovery-step.yml"
        )
    )
    assert shared["vars"]["k8s_database_step_source"] == "registered"
    custom = next(task for task in wrapped if "Add Plane's release" in task.get("name", ""))
    dumped = str(custom["vars"])
    assert "k8s_database_step.dump | replace('${DB_NAME}.dump', 'plane.dump')" in dumped
    assert "plane-release.txt" in dumped
    assert "redis-cli" in dumped and "redis-check-rdb" in dumped
    assert "k8s_database_step.load" in dumped

    restore = load_tasks("tasks/restore-kubernetes.yml")
    assert any(
        str(task.get("ansible.builtin.include_tasks", "")).endswith(
            "recovery/require-recent-pre-restore-point.yml"
        )
        for task in restore
    )
    assert any("distinct database and Redis targets" in task.get("name", "") for task in restore)
    restore_task = next(
        task for task in restore
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("recovery-restore.yml")
    )
    restore_inputs = restore_task["vars"]["k8s_recovery"]
    assert [archive["name"] for archive in restore_inputs["archives"]] == [
        "redis", "data", "object-storage"
    ]
    assert restore_inputs["database"] == "{{ _plane_k8s_database_step }}"
    assert restore_inputs["after_restore_tasks"] == "{{ role_path }}/tasks/restore-kubernetes-redis.yml"
    assert {name.replace("{{ restore_target }}", "{{ instance }}") for name in restore_inputs["stop"]} == set(recovery["stop"])

    redis_restore = (ROLE / "tasks/restore-kubernetes-redis.yml").read_text(encoding="utf-8")
    assert "redis-check-rdb" in redis_restore
    assert "kubectl" in redis_restore and "restored.rdb" in redis_restore
    assert "restore-redis-install.yml" in redis_restore
    assert "restore-kubernetes-rollback.yml" in redis_restore
    assert "state: absent" in redis_restore
    redis_install = (ROLE / "tasks/restore-redis-install.yml").read_text(encoding="utf-8")
    redis_rollback = (ROLE / "tasks/restore-redis-rollback.yml").read_text(encoding="utf-8")
    assert "live.rdb" in redis_install and "restore-redis-ready.yml" in redis_install
    assert "live.rdb" in redis_rollback and "restore-redis-ready.yml" in redis_rollback

    ready_job = documents(
        "ansible/roles/plane/templates/redis-ready-job.yaml.j2",
        {
            "restore_target": "plane",
            "restore_target_config": {"backup": {"redis_client_image": "redis:7-alpine"}},
            "app_config": {"backup": {"redis_client_image": "redis:7-alpine"}},
            "_plane_redis_backend": {"host": "redis.example.test", "port": 6379},
        },
    )[0]
    ready_container = ready_job["spec"]["template"]["spec"]["containers"][0]
    assert ready_container["command"] == ["/bin/sh", "-ceu"]
    assert 'test "$(redis-cli --no-auth-warning -h "$REDIS_HOST" -p "$REDIS_PORT" ping)" = PONG' in ready_container["args"][0]
    ready_env = {item["name"]: item for item in ready_container["env"]}
    assert ready_env["REDIS_HOST"]["value"] == "redis.example.test"
    assert ready_env["REDIS_PORT"]["value"] == "6379"
    assert ready_env["REDISCLI_AUTH"]["valueFrom"]["secretKeyRef"]["key"] == "redis-password"

    swap = swap_inputs("plane", "restore-docker.yml")
    assert swap["recovery_swap_archives"] == [
        "database.pxar", "redis.pxar", "data.pxar", "object-storage.pxar"
    ]
    assert swap["recovery_swap_database"]["dump"] == "database/plane.dump"


def test_shared_redis_backend_include():
    hooks = load_tasks("tasks/restore-kubernetes-redis.yml")[0]["block"]
    replace = next(t for t in hooks if "Replace the registered Redis" in t.get("name", ""))
    install = replace["block"][0]
    shared_register = load_tasks("tasks/restore-redis-install.yml")[0]
    with tempfile.TemporaryDirectory(prefix="plane-k8s-redis-") as directory:
        root = Path(directory)
        stub = root / "install.yml"
        stub.write_text(yaml.safe_dump([shared_register, {
            "ansible.builtin.assert": {
                "that": ["hostvars['plane_redis_restore_target'].ansible_host == 'redis.example.test'"]
            }
        }]))
        install["ansible.builtin.include_tasks"] = str(stub)
        play = root / "redis-regression.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "gather_facts": False,
            "vars": {"_plane_redis_backend": {"host": "redis.example.test"},
                     "homelabinfra_config": {}},
            "tasks": [install],
        }]))
        result = subprocess.run(
            [str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
             "-i", "localhost,", "-c", "local", str(play)],
            capture_output=True, text=True, timeout=90, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_recovery_client_packages():
    tasks = load_tasks("tasks/kubernetes-database-step.yml")
    task = next(t for t in tasks if "Add Plane's release" in t.get("name", ""))
    dump = task["vars"]["_plane_k8s_plane_dump"]
    install = dump[dump.index("if ! command -v redis-cli"):dump.index("mkdir -p /volumes/redis")]
    for available, expected in [
        (["apk"], ["add --no-cache redis"]),
        (["apt-get"], ["update -qq", "install -y -qq --no-install-recommends redis-tools"]),
        (["redis-cli", "redis-check-rdb"], []),
        ([], None),
    ]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "packages.log"
            for command in available:
                executable = root / command
                executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PLANE_TOOL_LOG"\n')
                executable.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", "-eu", "-c", install], capture_output=True, text=True,
                env={**os.environ, "PATH": directory, "PLANE_TOOL_LOG": str(log)},
            )
            if expected is None:
                assert result.returncode != 0
                assert "no supported package manager" in result.stdout
            else:
                assert result.returncode == 0, result.stderr
                assert (log.read_text().splitlines() if log.exists() else []) == expected


def main():
    test_shared_redis_backend_include()
    test_recovery_client_packages()
    test_runtime_environment_include()
    test_defaults_and_dispatch()
    test_workload_manifest()
    test_shared_recovery_contract()
    print("Plane Kubernetes: Docker default, workloads, registered backends and shared recovery passed")


if __name__ == "__main__":
    main()
