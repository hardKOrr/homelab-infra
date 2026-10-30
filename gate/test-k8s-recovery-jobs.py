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
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar

ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
# The database step reads the shared client script through the `file` lookup.
init_plugin_loader()
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
    "bookstack": {"role": "bookstack-kubernetes", "file": "main.yml", "restore": "restore.yml", "engine": "mariadb",
                  "archives": ["storage"]},
    "maintainerr": {"file": "main.yml", "restore": "restore.yml", "engine": None,
                    "archives": ["config"]},
    "jellyseerr": {"file": "main.yml", "restore": "restore.yml", "engine": None,
                   "archives": ["config"]},
    "hi-events": {"file": "main.yml", "restore": "restore.yml", "engine": "postgresql",
                  "archives": ["storage"]},
    "litellm": {"file": "main.yml", "restore": "restore.yml", "engine": "postgresql",
                "archives": ["config"]},
    "mixpost": {"file": "main.yml", "restore": "restore.yml", "engine": "namespace-mysql",
                "archives": ["storage"]},
    "n8n": {"file": "kubernetes.yml", "restore": "restore-kubernetes.yml",
            "engine": "postgresql", "archives": ["data"]},
    "karakeep": {"file": "kubernetes.yml", "restore": "restore-kubernetes.yml",
                 "engine": None, "archives": ["data"], "stop": ["src"]},
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
    """Render the step's real set_facts and return one step per database kind."""
    tasks = tasks_of(STEP.relative_to(ROOT))
    registered = task_named(tasks, "Database recovery step | Read the application login")
    namespace = task_named(tasks, "Database recovery step | Read the in-namespace server")
    describe = task_named(tasks, "Database recovery step | Describe the step")

    def step(kds):
        return render(describe["ansible.builtin.set_fact"]["k8s_database_step"], {
            "_kds": kds, "_kds_glob": render(describe["vars"]["_kds_glob"], {"_kds": kds}),
            "ansible_search_path": [str(STEP.parent)],
        })

    def target(provider, image):
        return render(registered["ansible.builtin.set_fact"]["_kds"], {
            "database_app_target": {
                "provider": provider, "instance": f"{provider}-app", "host": "192.0.2.10",
                "port": 5432 if provider == "postgresql" else 3306, "name": "app",
                "role": "app", "password": "", "client_image": image,
            },
            # The canonical item's password is read through the instance's merged config.
            "k8s_database_step_config": {"app": {"database_password": "fixture-db-password"}},
        })

    postgres = step(target("postgresql", "docker.io/library/postgres:16-alpine"))
    mariadb = step(target("mariadb", "docker.io/library/mariadb:11.4"))
    mysql = step(render(namespace["ansible.builtin.set_fact"]["_kds"], {
        "k8s_database_step_instance": "mp",
        "k8s_database_step_root_password": "root-secret",
        "k8s_database_step_config": {"app": {"database": {"name": "mixpost",
                                                          "image": "mysql:8.4"}}},
    }))

    script = (STEP.parent / "scripts/database-client.sh").read_text(encoding="utf-8")
    for database in (postgres, mariadb, mysql):
        # One client for guests and Jobs: the script ships in the Job's Secret.
        assert database["secret_data"]["database-client.sh"] == script.rstrip("\n")
        assert database["secret_files"] == [{"key": "database-client.sh",
                                             "path": "database-client.sh"}]
        assert database["secret_env"] == {"DB_PASSWORD": "db-password"}
        for body in ("dump", "load", "inspect"):
            assert "/etc/recovery-credentials/database-client.sh" in database[body], body
        load = database["load"]
        assert load.index(" verify ") < load.index(" restore ")
    # The client is the server's own image, so pg_restore accepts what pg_dump wrote.
    assert postgres["image"] == "docker.io/library/postgres:16-alpine"
    assert postgres["env"] == {"DB_ENGINE": "postgresql", "DB_HOST": "192.0.2.10",
                               "DB_PORT": "5432", "DB_NAME": "app", "DB_USER": "app"}
    assert postgres["secret_data"]["db-password"] == "fixture-db-password"
    # Existing recovery points hold these file names.
    assert postgres["dump"].endswith('dump "${DUMP_DIR}/${DB_NAME}.dump"')
    # A drill target's database name need not be the source's, whose name the dump carries.
    assert 'set -- "${DUMP_DIR}"/*.dump' in postgres["load"]
    assert mariadb["image"] == "docker.io/library/mariadb:11.4"
    assert mariadb["env"]["DB_ENGINE"] == "mariadb"
    assert mariadb["dump"].endswith('dump "${DUMP_DIR}/database.sql"')
    assert mysql["image"] == "mysql:8.4"
    assert mysql["env"] == {"DB_ENGINE": "mysql", "DB_HOST": "mp-db", "DB_PORT": "3306",
                            "DB_NAME": "mixpost", "DB_USER": "root"}
    assert mysql["secret_data"]["db-password"] == "root-secret"
    assert 'set -- "${DUMP_DIR}"/database.sql' in mysql["load"]
    return {"postgresql": postgres, "namespace-mysql": mysql, "mariadb": mariadb}


def backup_render(archives, database, instance="app", stop=None):
    kr = {
        "backup_id": instance, "repository": "token@pbs:store", "datastore": "store",
        "schedule": "0 3 * * *", "timezone": "UTC", "timeout": 3600,
        "retention_days": 14, "scratch_gi": 2, "secret": f"{instance}-backup",
        "ntfy_url": "", "ntfy_token": False, "client": CLIENT, "archives": archives,
        "database": database, "stop": stop or [],
    }
    text = render(BACKUP.read_text(encoding="utf-8"), {
        "_kr": kr, "instance": instance, "k8s_namespace": f"app-{instance}",
        "k8s_pbs_fingerprint": FINGERPRINT,
        "playbook_dir": str(ROOT / "ansible/playbooks/apps"),
    })
    return list(yaml.safe_load_all(text))


def backup_documents(archives, database, instance="app", stop=None):
    documents = backup_render(archives, database, instance, stop)
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
    assert pod["initContainers"][0]["image"] == "docker.io/library/postgres:16-alpine"
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
    ordinary_pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert "initContainers" not in ordinary_pod
    assert ordinary_pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in ordinary_pod

    quiesced = backup_render([{"name": "data", "claim": "app-storage"}], {},
                            "app", ["app"])
    service_account = next(d for d in quiesced if d["kind"] == "ServiceAccount")
    role = next(d for d in quiesced if d["kind"] == "Role")
    binding = next(d for d in quiesced if d["kind"] == "RoleBinding")
    cronjob = next(d for d in quiesced if d["kind"] == "CronJob")
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert service_account["metadata"]["name"] == "app-backup"
    assert role["rules"] == [
        {"apiGroups": ["apps"], "resources": ["deployments"],
         "resourceNames": ["app"], "verbs": ["get"]},
        {"apiGroups": ["apps"], "resources": ["deployments/scale"],
         "resourceNames": ["app"], "verbs": ["get", "patch"]},
    ]
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "app-backup",
                                     "namespace": "app-app"}]
    assert pod["serviceAccountName"] == "app-backup"
    assert pod["automountServiceAccountToken"] is True
    assert pod["terminationGracePeriodSeconds"] == 330
    push = pod["containers"][0]
    assert "kubernetes-recovery-scale.py stop" in push["args"][0]
    assert "kubernetes-recovery-scale.py start" in push["args"][0]
    assert 'K8S_RECOVERY_DEPLOYMENTS' in [entry["name"] for entry in push["env"]]

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
    assert "database-client.sh restore" in load
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
                                steps["namespace-mysql"])
    load = containers[1]
    assert load["image"] == "mysql:8.4"
    assert {"name": "credentials", "mountPath": "/etc/recovery-credentials",
            "readOnly": True} in load["volumeMounts"]
    assert {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {
        "name": "app-restore", "key": "db-password"}}} in load["env"]

    _, cronjob = backup_documents(
        [{"name": "storage", "claim": "bookstack-config"}], steps["mariadb"], "bookstack"
    )
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    dump = pod["initContainers"][0]
    assert dump["image"] == "docker.io/library/mariadb:11.4"
    assert "database-client.sh dump" in dump["args"][0]
    assert "database.pxar:/recovery/database" in pod["containers"][0]["args"][0]
    assert "storage.pxar:/volumes/storage" in pod["containers"][0]["args"][0]


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
    # The same server-side apply as Deploy, so the next Deploy reports no change.
    assert "--server-side" in apply["ansible.builtin.command"]["argv"]
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
        "playbook_dir": str(ROOT / "ansible/playbooks/apps"),
        "karakeep_fqdn": "src.example.test",
        "karakeep_data_uid": 1000,
        "karakeep_data_gid": 1000,
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
    if app == "karakeep":
        variables["app_config"]["app"].update({
            "image": "ghcr.io/karakeep-app/karakeep:0.33.2",
            "chrome_image": "ghcr.io/karakeep-app/karakeep-chrome:release",
            "meilisearch_image": "getmeili/meilisearch:v1.41.0",
            "port": 3005,
            "storage": 10,
            "disable_signups": False,
            "db_wal_mode": True,
        })
        manifest = render(manifest, variables)
        documents = list(yaml.safe_load_all(manifest))
        claim = next(d for d in documents if d["kind"] == "PersistentVolumeClaim")
        assert claim["metadata"]["name"] == "src-storage"
        assert "storageClassName" not in claim["spec"]
        assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
        deployment = next(d for d in documents if d["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        assert [container["name"] for container in pod["containers"]] == [
            "web", "chrome", "meilisearch"
        ]
        mounts = {container["name"]: container.get("volumeMounts", [])
                  for container in pod["containers"]}
        assert {m["subPath"] for m in mounts["web"]} == {"data"}
        assert {m["subPath"] for m in mounts["meilisearch"]} == {"meilisearch"}
        assert "http://localhost:7700" in str(pod["containers"][0]["env"])
        assert "http://localhost:9222" in str(pod["containers"][0]["env"])
        service = next(d for d in documents if d["kind"] == "Service")
        assert service["spec"]["ports"][0] == {"port": 3005, "targetPort": "web"}
        ingress = next(d for d in documents if d["kind"] == "Ingress")
        assert ingress["spec"]["rules"][0]["host"] == "src.example.test"
    assert [a["name"] for a in backup["archives"]] == spec["archives"], app
    assert [a["name"] for a in restore["archives"]] == spec["archives"], app
    assert backup.get("stop", []) == spec.get("stop", []), app
    for source, target in zip(backup["archives"], restore["archives"]):
        kind = "claim" if "claim" in source else "config_map"
        expected_source = (source[kind] if app == "karakeep"
                           else source[kind].replace("src", "{{ instance }}", 1))
        assert expected_source in manifest, (app, source)
        assert target[kind] == source[kind].replace("src", "dst", 1), (app, target)
    assert bool(backup.get("database")) == bool(spec["engine"]), app
    assert bool(restore.get("database")) == bool(spec["engine"]), app
    assert restore["stop"] == ["dst"], app
    if restore.get("after_restore_tasks"):
        assert Path(restore["after_restore_tasks"]).is_file(), app

    _, cronjob = backup_documents(backup["archives"], backup.get("database") or {}, "src",
                                  backup.get("stop"))
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
