#!/usr/bin/env python3
"""Render Odoo's Kubernetes adapter and check its portable recovery contract."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/odoo"


def tasks(name):
    return yaml.safe_load((ROLE / "tasks" / name).read_text())


def stub_database(root):
    (root / "psycopg2.py").write_text('''import os
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def execute(self, sql, args=None):
        if args is not None: assert isinstance(args[0], list)
    def fetchone(self): return (None if os.environ['PROBE_STATE'] == 'absent' else 'ir_module_module',)
    def fetchall(self): return [(name,) for name in os.environ['PROBE_STATE'].split(',')]
def connect(**kwargs):
    assert kwargs['dbname'] == 'odoo'
    if os.environ['PROBE_STATE'] == 'error': raise RuntimeError('connection failed')
    return Connection()
''')


def exercise_initializer(script):
    with tempfile.TemporaryDirectory(prefix="odoo-init-") as directory:
        root = Path(directory)
        stub = root / "odoo"
        stub.write_text('#!/bin/sh\nexit "${ODOO_TEST_EXIT:-0}"\n')
        stub.chmod(0o700)
        stub_database(root)
        config = root / "odoo.conf"
        config.write_text("[options]\ndb_host=localhost\ndb_port=5432\ndb_user=odoo\ndb_password=test\n")
        script = script.replace('/etc/odoo/odoo.conf', str(config))
        marker = root / ".crm-modules"
        script = script.replace("/var/lib/odoo", directory)
        environment = os.environ | {"PATH": directory + ":" + os.environ["PATH"],
                                    "ODOO_MODULES": "crm", "ODOO_DATABASE": "odoo",
                                    "PYTHONPATH": directory, "PROBE_STATE": "crm"}
        def run(extra=None):
            return subprocess.run(["sh", "-ec", script], env=environment | (extra or {}),
                                  capture_output=True, text=True, check=False)
        assert run().returncode == 0
        assert marker.read_text() == "crm\n"
        # A repeat skips initialization even when the CLI would fail.
        assert run({"ODOO_TEST_EXIT": "7"}).returncode == 0
        environment["ODOO_MODULES"] = "crm,sale"
        assert run({"ODOO_TEST_EXIT": "7"}).returncode == 7
        assert marker.read_text() == "crm\n"
        assert run().returncode == 0
        assert marker.read_text() == "crm,sale\n"
        # Matching filestore markers must not hide a dropped or partial database.
        for state in ['absent', 'crm']:
            assert run({"PROBE_STATE": state, "ODOO_TEST_EXIT": "7"}).returncode == 7
            assert marker.read_text() == "crm,sale\n"
            assert run({"PROBE_STATE": state}).returncode == 0
        assert run({"PROBE_STATE": "error"}).returncode != 0
        assert marker.read_text() == "crm,sale\n"
        assert run({"PROBE_STATE": "crm,sale", "ODOO_TEST_EXIT": "7"}).returncode == 0
        marker.unlink()
        assert run({"ODOO_TEST_EXIT": "7"}).returncode == 7
        assert not marker.exists()


def main():
    docker = tasks("docker.yml")
    probe = next(t for t in docker if t["name"] == "Odoo | Read database module readiness")
    initialize = next(t for t in docker if t["name"] == "Odoo | Initialize or extend the CRM module set")
    assert "_odoo_database_modules.stdout | trim != 'ready'" in initialize["when"]
    assert probe["changed_when"] is False and probe["no_log"] is True
    assert "database-module-readiness.py" in probe["ansible.builtin.command"]["argv"][-3]
    script = (ROLE / "files/database-module-readiness.py").read_text()
    with tempfile.TemporaryDirectory(prefix="odoo-database-probe-") as directory:
        root = Path(directory)
        config = root / "odoo.conf"
        config.write_text("[options]\ndb_host=localhost\ndb_port=5432\ndb_user=odoo\ndb_password=test\n")
        stub_database(root)
        for state, expected in [("absent", "initialize"), ("crm", "initialize"),
                                ("crm,sale", "ready"), ("error", None)]:
            result = subprocess.run(
                [sys.executable, "-c", script.replace('/etc/odoo/odoo.conf', str(config)), "odoo", "crm,sale"],
                env=os.environ | {"PYTHONPATH": directory, "PROBE_STATE": state},
                capture_output=True, text=True, check=False,
            )
            if expected is None:
                assert result.returncode != 0
            else:
                assert result.returncode == 0, result.stderr
                assert result.stdout.strip() == expected
    defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/odoo.yml").read_text())["odoo_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"
    assert [t["ansible.builtin.include_tasks"] for t in tasks("main.yml")] == ["docker.yml", "kubernetes.yml"]
    assert [t["ansible.builtin.include_tasks"] for t in tasks("restore.yml")] == ["restore-docker.yml", "restore-kubernetes.yml"]

    environment = Environment(trim_blocks=True, lstrip_blocks=True)
    environment.filters["to_json"] = json.dumps
    environment.filters["hash"] = lambda value, algorithm: hashlib.new(algorithm, value.encode()).hexdigest()
    environment.globals["lookup"] = lambda plugin, path: Path(path).read_text()
    source = (ROLE / "templates/manifest.yaml.j2").read_text()
    # Both default and estate-style names must render isolated objects and safe shell args.
    for instance, modules in [("odoo", "crm"), ("odoo-second", "crm,sale")]:
        config = defaults | {"routing": {"subdomain": instance}}
        documents = list(yaml.safe_load_all(environment.from_string(source).render(
            role_path=str(ROLE), instance=instance, app_config=config, homelabinfra_infra={"domain": "example.invalid"},
            _odoo_k8s_config="private test configuration", _odoo_modules=modules,
        )))
        objects = {d["kind"]: d for d in documents}
        assert len(documents) == len(objects) == 4
        for document in documents:
            assert document["metadata"]["labels"]["app.kubernetes.io/instance"] == instance
            assert "private test configuration" not in str(document)
        claim = objects["PersistentVolumeClaim"]
        assert claim["metadata"]["name"] == instance + "-filestore"
        assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert "storageClassName" not in claim["spec"]
        deployment = objects["Deployment"]
        assert deployment["spec"]["strategy"]["type"] == "Recreate"
        pod = deployment["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        container = pod["containers"][0]
        assert container["securityContext"] == {"runAsUser": 100, "runAsGroup": 101}
        assert container["readinessProbe"]["httpGet"]["path"] == "/web/health"
        assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == instance + "-filestore"
        assert pod["volumes"][1]["secret"]["defaultMode"] == 0o440
        initializer = pod["initContainers"][1]
        assert initializer["volumeMounts"] == container["volumeMounts"]
        assert initializer["env"][0]["value"] == modules
        script = initializer["args"][0]
        assert '--init="$ODOO_MODULES"' in script and "--stop-after-init" in script
        assert script.index("odoo --config") < script.index("printf")
        assert '!= "$ODOO_MODULES"' in script
        exercise_initializer(script)
        ingress = objects["Ingress"]["spec"]["rules"][0]
        assert ingress["host"] == instance + ".example.invalid"
        assert ingress["http"]["paths"][0]["backend"]["service"] == {
            "name": instance, "port": {"number": 8069}}

    kubernetes = tasks("kubernetes.yml")
    includes = {t.get("ansible.builtin.include_tasks"): t for t in kubernetes}
    for shared in ["resolve-cluster", "ensure-namespace", "open-app-access", "sync-secret", "apply-manifest", "recovery-backup"]:
        assert f"../../../tasks/kubernetes/{shared}.yml" in includes
    assert includes["../../../tasks/kubernetes/open-app-access.yml"]["vars"]["firewall_sends_mail"]
    db = "../../../tasks/database/kubernetes-recovery-step.yml"
    assert includes[db]["vars"]["k8s_database_step_source"] == "registered"
    backup = includes["../../../tasks/kubernetes/recovery-backup.yml"]["vars"]["k8s_recovery"]
    assert backup["archives"] == [{"name": "filestore", "claim": "{{ instance }}-filestore",
                                    "owner": "{{ app_config.app.puid }}:{{ app_config.app.pgid }}"}]
    assert "${DUMP_DIR}/odoo.dump" in backup["database"]
    assert backup["stop"] == ["{{ instance }}"]
    restore = tasks("restore-kubernetes.yml")
    assert restore[0]["when"] == "restore_destination == 'new'"
    assert "restore_source_database.name == restore_target_config.app.database.name" in str(restore[0])
    assert restore[1]["ansible.builtin.include_tasks"].endswith("require-recent-pre-restore-point.yml")
    assert restore[1]["when"] == "restore_overwrite | bool"
    assert restore[2]["vars"]["k8s_database_step_source"] == "registered"
    recovery = restore[3]["vars"]["k8s_recovery"]
    assert restore[3]["ansible.builtin.include_tasks"].endswith("/kubernetes/recovery-restore.yml")
    assert recovery["database"] == "{{ k8s_database_step }}"
    assert recovery["archives"][0]["name"] == "filestore"
    assert recovery["archives"][0]["claim"] == "{{ restore_target }}-filestore"
    assert recovery["stop"] == ["{{ restore_target }}"]
    playbook = (ROOT / "ansible/playbooks/apps/odoo.yml").read_text()
    assert "ingress_vip" in playbook
    assert "else _odoo_host.ansible_host" in playbook
    print("Odoo Kubernetes: workload, initialization, external database and portable recovery passed")


if __name__ == "__main__":
    main()
