#!/usr/bin/env python3
"""Render the shared Kubernetes backup CronJob and restore Job, then each app's use of them.

The shared pair owns the PBS client environment, its tmpfs, the PBS trust, recovery-point
identity and retention, the read-only preview and the restore order. Each app supplies
archives, an optional database step, owner and the Deployments to stop; the per-app check
renders the app's own inputs through the shared pair.
"""

import os
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
BACKUP = ROOT / "ansible/templates/kubernetes/recovery-backup.yaml.j2"
RESTORE = ROOT / "ansible/templates/kubernetes/recovery-restore.yaml.j2"
STEP = ROOT / "ansible/tasks/database/kubernetes-recovery-step.yml"
FINGERPRINT = ":".join(["AA"] * 32)
CLIENT = {
    "image": "buildpack-deps:bookworm-curl",
    "key_url": "https://example.test/proxmox-release.gpg",
    "repository": "http://example.test/debian/pbs-client",
    "suite": "bookworm",
    "component": "main",
}
# Archive names are part of every recovery point already in PBS. Changing one strands them.
APPS = {
    "bookstack": {"role": "bookstack-kubernetes", "file": "main.yml", "restore": "restore.yml", "engine": "external-mysql",
                  "archives": ["storage"]},
    "maintainerr": {"file": "main.yml", "restore": "restore.yml", "engine": None,
                    "archives": ["config"]},
    "jellyseerr": {"file": "main.yml", "restore": "restore.yml", "engine": None,
                   "archives": ["config"]},
    "hi-events": {"file": "main.yml", "restore": "restore.yml", "engine": "postgresql",
                  "archives": ["storage"]},
    "litellm": {"file": "main.yml", "restore": "restore.yml", "engine": "postgresql",
                "archives": ["config"]},
    "mixpost": {"file": "main.yml", "restore": "restore.yml", "engine": "mysql",
                "archives": ["storage"]},
    "n8n": {"file": "kubernetes.yml", "restore": "restore-kubernetes.yml",
            "engine": "postgresql", "archives": ["data"]},
}


def render(text, variables):
    return Templar(loader=DataLoader(), variables=variables).template(text)


def flatten(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from flatten(task.get(key))


def tasks_of(path):
    return list(flatten(yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))))


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def database_steps():
    tasks = tasks_of(STEP.relative_to(ROOT))
    postgres = task_named(tasks, "Database recovery step | Describe the PostgreSQL step")
    mysql = task_named(tasks, "Database recovery step | Describe the MySQL step")
    postgres = render(postgres["ansible.builtin.set_fact"]["k8s_database_step"], {
        "_kds_version": "16", "_kds_host": "pg.example.test", "_kds_port": "5432",
        "_kds_name": "app", "_kds_user": "app", "_kds_password": "secret",
    })
    mysql = render(mysql["ansible.builtin.set_fact"]["k8s_database_step"], {
        "k8s_database_step_instance": "mp",
        "k8s_database_step_root_password": "root-secret",
        "k8s_database_step_config": {"app": {"database": {"name": "mixpost",
                                                          "image": "mysql:8.4"}}},
    })
    # The client is the server's major, so pg_restore accepts what pg_dump wrote.
    assert postgres["image"] == "postgres:16-alpine"
    assert postgres["secret_env"] == {"PGPASSWORD": "db-password"}
    assert '"${DUMP_DIR}/${PGDATABASE}.dump"' in postgres["dump"]
    # A drill target's database name need not be the source's, whose name the dump carries.
    assert 'set -- "${DUMP_DIR}"/*.dump' in postgres["load"]
    assert "pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error" in postgres["load"]
    assert mysql["image"] == "mysql:8.4"
    assert mysql["env"]["DB_HOST"] == "mp-db"
    assert mysql["secret_files"] == [{"key": "my.cnf", "path": "my.cnf"}]
    assert "password=root-secret" in mysql["secret_data"]["my.cnf"]
    assert '"${DUMP_DIR}/database.sql"' in mysql["dump"]
    assert "DROP DATABASE IF EXISTS" in mysql["load"]

    external_task = task_named(tasks, "Database recovery step | Describe the external database operations")

    def external_mysql(provider):
        client = "mariadb" if provider == "mariadb" else "mysql"
        dump_client = "mariadb-dump" if provider == "mariadb" else "mysqldump"
        package = "mariadb-client" if provider == "mariadb" else "default-mysql-client"
        return render(external_task["ansible.builtin.set_fact"]["k8s_database_step"], {
            "_kds_provider": provider,
            "_kds_client": client,
            "_kds_dump_client": dump_client,
            "_kds_client_package": package,
            "_kds_host": "database.example.test",
            "_kds_port": "3306",
            "_kds_name": "bookstack",
            "_kds_user": "bookstack",
            "_kds_password": "fixture-database-password",
        })

    external_mariadb = external_mysql("mariadb")
    assert external_mariadb["image"] == "debian:bookworm-slim"
    assert "mariadb-client" in external_mariadb["setup"]
    assert external_mariadb["env"] == {
        "DB_HOST": "database.example.test", "DB_PORT": "3306", "DB_NAME": "bookstack"
    }
    assert external_mariadb["secret_files"] == [{"key": "my.cnf", "path": "my.cnf"}]
    assert "user=bookstack" in external_mariadb["secret_data"]["my.cnf"]
    assert "mariadb-dump" in external_mariadb["dump"]
    assert "--single-transaction" in external_mariadb["dump"]
    assert "DROP TABLE IF EXISTS" in external_mariadb["load"]
    assert "DROP DATABASE" not in external_mariadb["load"]
    assert "mariadb" in external_mariadb["inspect"]

    external_mysql_step = external_mysql("mysql")
    assert "default-mysql-client" in external_mysql_step["setup"]
    assert "mysqldump" in external_mysql_step["dump"]
    return {"postgresql": postgres, "mysql": mysql, "external-mysql": external_mariadb}


def backup_documents(archives, database, instance="app"):
    kr = {
        "backup_id": instance, "repository": "token@pbs:store", "datastore": "store",
        "schedule": "0 3 * * *", "timezone": "UTC", "timeout": 3600,
        "retention_days": 14, "scratch_gi": 2, "secret": f"{instance}-backup",
        "ntfy_url": "", "ntfy_token": False, "client": CLIENT, "archives": archives,
        "database": database,
    }
    text = render(BACKUP.read_text(encoding="utf-8"), {
        "_kr": kr, "instance": instance, "k8s_namespace": f"app-{instance}",
        "k8s_pbs_fingerprint": FINGERPRINT,
    })
    documents = list(yaml.safe_load_all(text))
    return (next(d for d in documents if d["kind"] == "ConfigMap"),
            next(d for d in documents if d["kind"] == "CronJob"))


def restore_pod(mode, archives, database, target="app"):
    kr = {
        "mode": mode, "job_name": f"{target}-restore-{mode}", "target": target,
        "timeout": 900, "secret": f"{target}-restore", "backup_id": "source",
        "snapshot": "host/source/2026-10-27T03:00:00Z", "scratch_gi": 2, "client": CLIENT,
        "archives": archives, "database": database,
    }
    job = yaml.safe_load(render(RESTORE.read_text(encoding="utf-8"), {
        "_kr": kr, "k8s_pbs_repository": "token@pbs:store",
        "k8s_pbs_fingerprint": FINGERPRINT,
    }))
    assert job["kind"] == "Job" and job["spec"]["backoffLimit"] == 0
    pod = job["spec"]["template"]["spec"]
    return pod, pod.get("initContainers", []) + pod["containers"]


def assert_client_tmpfs(pod, container):
    # The client's cache and temporary files need O_TMPFILE, which the container overlay
    # refuses with os error 95.
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    assert mounts["client-tmp"] == "/tmp" and mounts["client-cache"] == "/root/.cache"
    volumes = {v["name"]: v for v in pod["volumes"]}
    for name in ("client-tmp", "client-cache"):
        assert volumes[name]["emptyDir"] == {"medium": "Memory", "sizeLimit": "64Mi"}


def shared_pair(steps):
    postgres = steps["postgresql"]
    archives = [{"name": "data", "claim": "app-storage", "owner": "1000:1000"},
                {"name": "config", "config_map": "app-config"}]

    trust, cronjob = backup_documents(archives, postgres)
    assert trust["metadata"]["name"] == "pbs-backup-trust"
    assert trust["data"]["fingerprint"] == FINGERPRINT
    for document in (trust, cronjob):
        assert document["metadata"]["labels"]["homelab-infra.io/pbs-fingerprint"] == "managed"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    assert cronjob["spec"]["timeZone"] == "UTC"
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["initContainers"]] == ["dump"]
    assert pod["initContainers"][0]["image"] == "postgres:16-alpine"
    push = pod["containers"][0]
    script = push["args"][0]
    assert_client_tmpfs(pod, push)
    env = {e["name"]: e for e in push["env"]}
    # Deploy PBS refreshes this ConfigMap; a months-old CronJob follows a renewed cert.
    assert env["PBS_FINGERPRINT"]["valueFrom"]["configMapKeyRef"] == {
        "name": "pbs-backup-trust", "key": "fingerprint"}
    assert env["PBS_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "app-backup"
    for archive in ("database.pxar:/recovery/database", "data.pxar:/volumes/data",
                    "config.pxar:/volumes/config"):
        assert archive in script, archive
    # A same-day pre-restore backup must not prune the point being restored.
    assert '--keep-last 2 --keep-daily "${RETENTION_DAYS}"' in script
    assert all(m.get("readOnly") for m in push["volumeMounts"]
               if m["name"].startswith("volume-"))
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["volume-data"]["persistentVolumeClaim"] == {
        "claimName": "app-storage", "readOnly": True}
    assert volumes["volume-config"]["configMap"]["name"] == "app-config"

    _, cronjob = backup_documents([{"name": "config", "claim": "app-data"}], {})
    assert "initContainers" not in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]

    # Plan: the same client path into scratch, with the target read-only.
    pod, containers = restore_pod("plan", archives, postgres)
    assert [c["name"] for c in containers] == ["preview", "inspect"]
    preview = containers[0]["args"][0]
    assert 'proxmox-backup-client restore "${SNAPSHOT}" index.json /recovery/index.json' in preview
    assert 'proxmox-backup-client catalog dump "${SNAPSHOT}"' in preview
    assert "find /volumes" not in preview.replace("find \"$1\"", "")
    assert_client_tmpfs(pod, containers[0])
    assert all(m.get("readOnly") for m in containers[0]["volumeMounts"]
               if m["name"].startswith("volume-"))
    claims = [v["persistentVolumeClaim"] for v in pod["volumes"] if "persistentVolumeClaim" in v]
    assert claims == [{"claimName": "app-storage", "readOnly": True}]

    # Restore: the database loads before any volume is touched.
    pod, containers = restore_pod("restore", archives, postgres)
    assert [c["name"] for c in containers] == ["stage", "load", "replace"]
    assert [c["name"] for c in pod["initContainers"]] == ["stage", "load"]
    stage, load, replace = (c["args"][0] for c in containers)
    assert 'restore "${SNAPSHOT}" database.pxar /recovery/database' in stage
    assert 'restore "${SNAPSHOT}" config.pxar /recovery/config' in stage
    assert "RECOVERY_CONFIGMAP config" in stage
    assert "/volumes/" not in stage
    assert "pg_restore" in load
    assert replace.index("find /volumes/data -mindepth 1 -delete") < replace.index(
        'restore "${SNAPSHOT}" data.pxar /volumes/data')
    assert "chown -R 1000:1000 /volumes/data" in replace
    for container in (containers[0], containers[2]):
        assert_client_tmpfs(pod, container)
    claims = [v["persistentVolumeClaim"] for v in pod["volumes"] if "persistentVolumeClaim" in v]
    assert claims == [{"claimName": "app-storage"}]

    _, containers = restore_pod("restore", [{"name": "config", "claim": "app-data"}], {})
    assert [c["name"] for c in containers] == ["replace"]
    _, containers = restore_pod("restore", [{"name": "config", "config_map": "app-config"}],
                                postgres)
    assert [c["name"] for c in containers] == ["stage", "load"]

    _, containers = restore_pod("restore", [{"name": "storage", "claim": "app-storage"}],
                                steps["mysql"])
    load = containers[1]
    assert load["image"] == "mysql:8.4"
    assert {"name": "credentials", "mountPath": "/etc/recovery-credentials",
            "readOnly": True} in load["volumeMounts"]

    _, cronjob = backup_documents(
        [{"name": "storage", "claim": "bookstack-config"}], steps["external-mysql"], "bookstack"
    )
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    dump = pod["initContainers"][0]
    assert "mariadb-client" in dump["args"][0]
    assert "mariadb-dump" in dump["args"][0]
    assert "database.pxar:/recovery/database" in pod["containers"][0]["args"][0]
    assert "storage.pxar:/volumes/storage" in pod["containers"][0]["args"][0]

    _, containers = restore_pod(
        "restore", [{"name": "storage", "claim": "bookstack-config", "owner": "1000:1000"}],
        steps["external-mysql"], "bookstack"
    )
    load = containers[1]
    assert "mariadb-client" in load["args"][0]
    assert "DROP TABLE IF EXISTS" in load["args"][0]
    assert "DROP DATABASE" not in load["args"][0]
    replace = containers[2]["args"][0]
    assert "find /volumes/storage -mindepth 1 -delete" in replace
    assert "chown -R 1000:1000 /volumes/storage" in replace


def restore_tasks():
    tasks = tasks_of("ansible/tasks/kubernetes/recovery-restore.yml")
    wait = task_named(tasks, "k8s restore | Wait for the application pods to go away")
    selector = next(arg for arg in wait["ansible.builtin.command"]["argv"]
                    if str(arg).startswith("--selector="))
    item = {"stdout": '{"app.kubernetes.io/component":"web","app.kubernetes.io/instance":"t"}'}
    assert render(selector, {"item": item}) == (
        "--selector=app.kubernetes.io/component=web,app.kubernetes.io/instance=t")

    read = task_named(tasks, "k8s restore | Read the restored ConfigMaps from the job's output")
    log = ("[pod/t-restore-run/stage] RECOVERY_CONFIGMAP config config.yaml YWJj\n"
           "[pod/t-restore-run/stage] RECOVERY_CONFIGMAP other x.yaml ZGVm\n")
    found = render(read["ansible.builtin.set_fact"]["_kr_configmaps"], {
        "k8s_job_log": log, "item": {"name": "config", "config_map": "t-config"}})
    found = {name: [list(match) for match in matches] for name, matches in found.items()}
    assert found == {"t-config": [["config.yaml", "YWJj"]]}, found
    apply = task_named(tasks, "k8s restore | Apply the restored ConfigMaps")
    manifest = yaml.safe_load(render(apply["ansible.builtin.command"]["stdin"], {
        "item": {"key": "t-config", "value": found["t-config"]}, "restore_target": "t"}))
    assert manifest["metadata"]["name"] == "t-config"
    assert manifest["data"] == {"config.yaml": "abc"}

    # A failed restore leaves every stopped Deployment at zero.
    keep = task_named(tasks, "k8s restore | Keep the target stopped")
    assert "--replicas=0" in keep["ansible.builtin.command"]["argv"]
    names = [task.get("name", "") for task in tasks]
    order = ["k8s restore | Stop the target application", "k8s restore | Run the restore job",
             "k8s restore | Apply the restored ConfigMaps",
             "k8s restore | Run the app's after-restore tasks",
             "k8s restore | Start the target application again"]
    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)


def include_vars(tasks, shared_file):
    include = next(task for task in tasks
                   if str(task.get("ansible.builtin.include_tasks", "")).endswith(shared_file))
    return include["vars"]["k8s_recovery"]


def app_render(app, spec, steps):
    role_name = spec.get("role", app)
    role = ROOT / "ansible/roles" / role_name
    manifest = (role / "templates/manifest.yaml.j2").read_text(encoding="utf-8")
    assert not (role / "templates/backup-cronjob.yaml.j2").exists()
    assert not (role / "templates/restore-job.yaml.j2").exists()
    database = steps[spec["engine"]] if spec["engine"] else {}
    variables = {
        "instance": "src", "restore_target": "dst", "k8s_database_step": database,
        "role_path": str(role),
        "app_config": {"app": {
            "database": {"name": "db", "provider": "mariadb"},
            "database_host": "database.example.test", "database_port": 3306,
            "database_user": "bookstack", "database_password": "fixture-password",
            "puid": 1000, "pgid": 1000,
        }},
        "restore_target_config": {"app": {
            "database": {"name": "db", "provider": "mariadb"},
            "database_host": "database.example.test", "database_port": 3306,
            "database_user": "bookstack", "database_password": "fixture-password",
            "puid": 1000, "pgid": 1000,
        }},
    }

    backup = render(include_vars(tasks_of(f"ansible/roles/{role_name}/tasks/{spec['file']}"),
                                 "recovery-backup.yml"), variables)
    restore = render(include_vars(tasks_of(f"ansible/roles/{role_name}/tasks/{spec['restore']}"),
                                  "recovery-restore.yml"), variables)
    assert [a["name"] for a in backup["archives"]] == spec["archives"], app
    assert [a["name"] for a in restore["archives"]] == spec["archives"], app
    for source, target in zip(backup["archives"], restore["archives"]):
        kind = "claim" if "claim" in source else "config_map"
        assert source[kind].replace("src", "{{ instance }}", 1) in manifest, (app, source)
        assert target[kind] == source[kind].replace("src", "dst", 1), (app, target)
    assert bool(backup.get("database")) == bool(spec["engine"]), app
    assert bool(restore.get("database")) == bool(spec["engine"]), app
    assert restore["stop"] == ["dst"], app
    if restore.get("after_restore_tasks"):
        assert Path(restore["after_restore_tasks"]).is_file(), app

    _, cronjob = backup_documents(backup["archives"], backup.get("database") or {}, "src")
    assert cronjob["metadata"]["name"] == "src-backup"
    for mode in ("plan", "restore"):
        restore_pod(mode, restore["archives"], restore.get("database") or {}, "dst")


def app_contracts():
    # A dump lands only in a compatible named database of the same major version.
    contract = task_named(tasks_of("ansible/roles/litellm/tasks/restore.yml"),
                          "Restore | Require matching PostgreSQL state contracts")
    that = " ".join(contract["ansible.builtin.assert"]["that"])
    assert "app_config.app.database.version" in that
    assert "restore_target_config.app.database.version" in that
    contract = task_named(tasks_of("ansible/roles/mixpost/tasks/restore.yml"),
                          "Restore | Require both instances to use the same database name")
    assert "restore_target_config.app.database.name" in contract["ansible.builtin.assert"]["that"]


def main():
    steps = database_steps()
    shared_pair(steps)
    restore_tasks()
    app_contracts()
    for app, spec in APPS.items():
        app_render(app, spec, steps)
    print(f"Kubernetes recovery jobs: the shared pair and {len(APPS)} apps render")


if __name__ == "__main__":
    main()
