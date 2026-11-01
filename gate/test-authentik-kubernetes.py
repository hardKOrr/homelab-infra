#!/usr/bin/env python3
"""Render isolated Authentik workloads and check recovery and default-method contracts."""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/authentik"


def tasks(name):
    return yaml.safe_load((ROLE / "tasks" / name).read_text())


def includes(name):
    return {task.get("ansible.builtin.include_tasks"): task for task in tasks(name)}


def check_registered_credential_guard():
    """Run the actual probe and guard with a stubbed backend, before secret generation."""
    main_tasks = {task["name"]: task for task in tasks("main.yml")}
    query = main_tasks["Check the registered database for the Authentik schema"].copy()
    for key in ["delegate_to", "become", "become_user"]:
        query.pop(key)
    check = main_tasks["Require a definitive registered database schema check"]
    guard = main_tasks["Refuse to replace credentials for an initialized registered database"]
    assert list(main_tasks).index(guard["name"]) < list(main_tasks).index(
        "Resolve secrets (reuse what exists, generate only what is missing)")
    with tempfile.TemporaryDirectory(prefix="authentik-credential-guard-") as temporary:
        root = Path(temporary)
        client = root / "psql"
        client.write_text("#!/bin/sh\ncase \"$*\" in *authentik_core_user*) ;; *) exit 9;; esac\n"
                          'case "$SCHEMA" in failed) exit 2;; *) printf "%s\\n" "$SCHEMA";; esac\n')
        client.chmod(0o755)
        query["ansible.builtin.command"] = {"argv": [str(client), *query["ansible.builtin.command"]["argv"][1:]]}
        complete = {"secret_key": "retained-signing-key", "admin_password": "retained-admin", "token": "retained-token"}
        cases = [("f", {}, True), ("t", complete, True), ("t", {}, False),
                 ("failed", complete, False), ("unknown", complete, False)]
        cases += [("t", complete | {field: ""}, False) for field in complete]
        for method in ["docker", "kubernetes"]:
            for schema, recorded, passes in cases:
                marker = root / "generation-reached"
                marker.unlink(missing_ok=True)
                play = [{"hosts": "localhost", "gather_facts": False,
                         "environment": {"SCHEMA": schema},
                         "vars": {"instance": "authentik-second", "_authentik_external_database": True,
                                  "app_config": {"hosting": {"method": method}},
                                  "database_app_target": {"name": "authentik_second", "port": 5432},
                                  "_authentik_recorded_sso": recorded,
                                  # Local .env cannot substitute for the canonical SSO item here.
                                  "_authentik_env_existing": {"AUTHENTIK_SECRET_KEY": "local-key"}},
                         "tasks": [query, check, guard,
                                   {"ansible.builtin.copy": {"dest": str(marker), "content": "reached"}}]}]
                path = root / "guard.yml"
                path.write_text(yaml.safe_dump(play))
                result = subprocess.run([str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
                                         "-i", "localhost,", "-c", "local", str(path)],
                                        capture_output=True, text=True, check=False)
                assert (result.returncode == 0) == passes, (method, schema, recorded.keys(), result.stdout, result.stderr)
                assert marker.exists() == passes


def main():
    check_registered_credential_guard()
    defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/authentik.yml").read_text())["authentik_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert set(defaults["hosting"]["supported"]) == {"docker-vm", "docker-lxc", "kubernetes"}
    # Exercise Ansible's eager defaults rendering: a self-reference here breaks
    # Deploy and maintenance before their hosting dispatch even runs.
    with tempfile.TemporaryDirectory(prefix="authentik-k8s-defaults-") as temporary:
        play = [{"hosts": "localhost", "gather_facts": False,
                 "vars": {"instance": "authentik-second"}, "tasks": [
                     {"ansible.builtin.include_vars": {"file": str(ROOT / "ansible/vars/app-defaults/authentik.yml")}},
                     {"ansible.builtin.set_fact": {"app_config": "{{ authentik_defaults }}"}},
                     {"ansible.builtin.assert": {"that": [
                         "app_config.hosting.method == 'docker'",
                         "app_config.app.database.instance == ''",
                         "app_config.app.database.name == 'authentik_second'",
                         "app_config.app.database.role == 'authentik_second'",
                     ]}},
                 ]}]
        path = Path(temporary) / "defaults.yml"
        path.write_text(yaml.safe_dump(play))
        result = subprocess.run([str(Path.home() / ".venvs/homelab-ansible/bin/ansible-playbook"),
                                 "-i", "localhost,", "-c", "local", str(path)],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "changed=0" in result.stdout
    env = Environment(trim_blocks=True, lstrip_blocks=True)
    env.filters["to_json"] = json.dumps
    env.filters["hash"] = lambda value, algorithm: hashlib.new(algorithm, value.encode()).hexdigest()
    template = env.from_string((ROLE / "templates/manifest.yaml.j2").read_text())
    for instance in ["authentik", "authentik-second"]:
        config = defaults | {"routing": {"subdomain": instance}}
        rendered = template.render(instance=instance, app_config=config,
                                   homelabinfra_infra={"domain": "example.invalid"},
                                   _authentik_tag="2025.10.1", _authentik_k8s_env={"password": "private-example"})
        assert "private-example" not in rendered
        documents = list(yaml.safe_load_all(rendered))
        objects = {d["kind"]: d for d in documents}
        assert len(documents) == len(objects) == 4
        for obj in documents:
            assert obj["metadata"]["labels"]["app.kubernetes.io/instance"] == instance
        claim = objects["PersistentVolumeClaim"]
        assert claim["metadata"]["name"] == instance + "-data"
        assert "storageClassName" not in claim["spec"]
        assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
        deployment = objects["Deployment"]
        assert deployment["spec"]["strategy"]["type"] == "Recreate"
        pod = deployment["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["volumes"] == [{"name": "data", "persistentVolumeClaim": {"claimName": instance + "-data"}}]
        containers = {c["name"]: c for c in pod["containers"]}
        assert set(containers) == {"server", "worker", "redis"}
        for name in ["server", "worker"]:
            container = containers[name]
            assert container["args"] == [name]
            assert container["image"] == "ghcr.io/goauthentik/server:2025.10.1"
            assert container["envFrom"] == [{"secretRef": {"name": instance + "-env"}}]
            assert {v["subPath"] for v in container["volumeMounts"]} == {"media", "certs", "custom-templates"}
        assert containers["server"]["securityContext"]["runAsUser"] == 1000
        assert containers["server"]["readinessProbe"]["httpGet"]["path"] == "/-/health/ready/"
        assert "volumeMounts" not in containers["redis"]
        assert not any("hostPath" in volume for volume in pod["volumes"])
        ingress = objects["Ingress"]["spec"]["rules"][0]
        assert ingress["host"] == instance + ".example.invalid"
        assert ingress["http"]["paths"][0]["backend"]["service"]["name"] == instance
        # Default Compose still owns PostgreSQL; opt-in registered mode never starts it.
        for external in [False, True]:
            compose = yaml.safe_load(env.from_string((ROLE / "templates/docker-compose.yml.j2").read_text()).render(
                instance=instance, app_config=config, _authentik_tag="2025.10.1",
                _authentik_external_database=external))
            services = compose["services"]
            assert ("postgresql" in services) is (not external)
            for name in ["server", "worker"]:
                assert ("postgresql" in services[name]["depends_on"]) is (not external)

    k8s = includes("kubernetes.yml")
    for shared in ["resolve-cluster", "ensure-namespace", "open-app-access", "sync-secret", "apply-manifest", "recovery-backup"]:
        assert f"../../../tasks/kubernetes/{shared}.yml" in k8s
    database = "../../../tasks/database/kubernetes-recovery-step.yml"
    assert k8s[database]["vars"]["k8s_database_step_source"] == "registered"
    backup = k8s["../../../tasks/kubernetes/recovery-backup.yml"]["vars"]["k8s_recovery"]
    restore = includes("restore-kubernetes.yml")
    assert restore[database]["vars"]["k8s_database_step_source"] == "registered"
    recovery = restore["../../../tasks/kubernetes/recovery-restore.yml"]["vars"]["k8s_recovery"]
    assert backup["stop"] == ["{{ instance }}"]
    assert recovery["stop"] == ["{{ restore_target }}"]
    assert backup["archives"] == [{"name": "data", "claim": "{{ instance }}-data", "owner": "1000:1000"}]
    assert recovery["archives"][0]["name"] == backup["archives"][0]["name"]
    registered = includes("restore-registered.yml")["../../../tasks/recovery/restore-swap.yml"]["vars"]
    assert registered["recovery_swap_archives"] == ["data.pxar", "database.pxar"]
    assert registered["recovery_swap_database"]["config"] == "{{ app_config.app.database }}"
    assert {p["source"] for p in registered["recovery_swap_paths"]} == {"data/media", "data/certs", "data/custom-templates"}
    backup_dispatch = includes("backup.yml")
    assert "database.instance" in backup_dispatch["backup-docker.yml"]["when"]
    assert "== 0" in backup_dispatch["backup-docker.yml"]["when"]
    dispatch = includes("restore.yml")
    assert "== 0" in str(dispatch["restore-docker.yml"]["when"])

    assert "kubernetes" in dispatch["restore-kubernetes.yml"]["when"]
    assert "database.instance" in str(dispatch["restore-registered.yml"]["when"])
    print("Authentik Kubernetes: isolated manifests, default Compose, shared registered database recovery passed.")


if __name__ == "__main__":
    main()
