#!/usr/bin/env python3
"""Check Mautic's Kubernetes workload, config convergence and shared recovery inputs."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml
from jinja2 import Environment
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
defaults = yaml.safe_load((ROOT / "ansible/vars/app-defaults/mautic.yml").read_text())[
    "mautic_defaults"
]
role_main = yaml.safe_load((ROOT / "ansible/roles/mautic/tasks/main.yml").read_text())
kubernetes_tasks = yaml.safe_load((ROOT / "ansible/roles/mautic/tasks/kubernetes.yml").read_text())
restore_wrapper = yaml.safe_load((ROOT / "ansible/roles/mautic/tasks/restore.yml").read_text())
restore_kubernetes = yaml.safe_load(
    (ROOT / "ansible/roles/mautic/tasks/restore-kubernetes.yml").read_text()
)
deploy_playbook = yaml.safe_load((ROOT / "ansible/playbooks/apps/mautic.yml").read_text())
template_source = (ROOT / "ansible/roles/mautic/templates/manifest.yaml.j2").read_text()


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def render_manifest():
    environment = Environment(trim_blocks=True, lstrip_blocks=True)
    environment.filters["to_json"] = lambda value: json.dumps(value)
    rendered = environment.from_string(template_source).render(
        instance="mautic",
        mautic_fqdn="mautic.example.invalid",
        app_config={
            "app": {
                "image": "docker.io/mautic/mautic:6.0.6-apache",
                "storage": 10,
                "database_host": "192.0.2.20",
                "database_port": 3306,
                "database_user": "mautic",
                "database": {"name": "mautic"},
            }
        },
    )
    return [document for document in yaml.safe_load_all(rendered) if document]


def static_contract():
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert role_main[0]["ansible.builtin.include_tasks"] == "docker.yml"
    assert role_main[0]["when"] == "app_config.hosting.method | default('docker') == 'docker'"
    assert role_main[1]["ansible.builtin.include_tasks"] == "kubernetes.yml"
    assert "ingress_vip" in (ROOT / "ansible/playbooks/apps/mautic.yml").read_text()

    includes = [task.get("ansible.builtin.include_tasks", "") for task in kubernetes_tasks]
    assert "../../../tasks/kubernetes/open-app-access.yml" in includes
    assert "../../../tasks/kubernetes/apply-manifest.yml" in includes
    assert "../../../tasks/database/kubernetes-recovery-step.yml" in includes
    assert "../../../tasks/kubernetes/recovery-backup.yml" in includes
    backup = task_named(kubernetes_tasks, "Mautic Kubernetes | Configure the shared application recovery CronJob")
    assert backup["vars"]["k8s_recovery"]["database"] == "{{ k8s_database_step | default({}) }}"
    assert backup["vars"]["k8s_recovery"]["archives"] == [{
        "name": "data", "claim": "{{ instance }}-data", "owner": "33:33"}]
    assert backup["vars"]["k8s_recovery"]["stop"] == [
        "{{ instance }}-web", "{{ instance }}-cron", "{{ instance }}-worker"]

    restore = task_named(restore_kubernetes, "Mautic restore | Use the shared Kubernetes recovery Job")
    restore_inputs = restore["vars"]["k8s_recovery"]
    assert restore_inputs["database"] == "{{ k8s_database_step }}"
    assert restore_inputs["archives"] == [{
        "name": "data", "claim": "{{ restore_target }}-data", "owner": "33:33"}]
    assert restore_inputs["stop"] == [
        "{{ restore_target }}-web", "{{ restore_target }}-cron", "{{ restore_target }}-worker"]
    assert any("restore-kubernetes.yml" in str(task) for task in restore_wrapper)

    assert defaults["backup"]["client"]["image"] == "buildpack-deps:bookworm-curl"
    database_firewall = task_named(kubernetes_tasks, "Mautic Kubernetes | Add access to the named MariaDB backend")
    assert "app_config.app.database_host" in str(database_firewall["ansible.builtin.set_fact"])
    assert "app_config.app.database_port" in str(database_firewall["ansible.builtin.set_fact"])


def rendered_workload():
    manifests = render_manifest()
    by_key = {(item["kind"], item["metadata"]["name"]): item for item in manifests}
    assert ("PersistentVolumeClaim", "mautic-data") in by_key
    claim = by_key[("PersistentVolumeClaim", "mautic-data")]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert "storageClassName" not in claim["spec"]
    init_script = by_key[("Deployment", "mautic-web")]["spec"]["template"]["spec"]["initContainers"][0]["args"][0]
    assert "chown 33:33 /data/config /data/logs /data/media /data/media/files /data/media/images" in init_script

    deployments = {
        name: by_key[("Deployment", name)]
        for name in ("mautic-web", "mautic-cron", "mautic-worker")
    }
    for name, role in (("mautic-web", "mautic_web"),
                       ("mautic-cron", "mautic_cron"),
                       ("mautic-worker", "mautic_worker")):
        pod = deployments[name]["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {item["name"]: item.get("value") for item in container["env"]}
        assert env["DOCKER_MAUTIC_ROLE"] == role
        assert env["MAUTIC_MESSENGER_DSN_EMAIL"] == "doctrine://default"
        assert env["MAUTIC_MESSENGER_DSN_HIT"] == "doctrine://default"
        assert env["MAUTIC_MESSENGER_DSN_FAILED"] == "doctrine://default"
        claim_names = [volume.get("persistentVolumeClaim", {}).get("claimName")
                       for volume in pod["volumes"]]
        assert "mautic-data" in claim_names
        mount_paths = {mount["mountPath"] for mount in container["volumeMounts"]}
        assert {
            "/var/www/html/config",
            "/var/www/html/var/logs",
            "/var/www/html/docroot/media/files",
            "/var/www/html/docroot/media/images",
        } <= mount_paths

    ingress = by_key[("Ingress", "mautic")]
    assert ingress["spec"]["rules"][0]["host"] == "mautic.example.invalid"
    assert by_key[("Service", "mautic")]["spec"]["selector"]["app.kubernetes.io/component"] == "web"


def installer_arguments():
    task = task_named(kubernetes_tasks, "Mautic Kubernetes | Run the non-interactive installer")
    password = "a password with ' quotes % and $(touch injected)"
    argv = Templar(loader=DataLoader(), variables={
        "instance": "mautic",
        "k8s_namespace": "app-mautic", "mautic_fqdn": "mautic.example.invalid",
        "app_config": {"app": {"admin_user": "drill-admin"}},
        "homelabinfra_infra": {"domain": "example.invalid"},
        "_mautic_admin_password": password,
    }).template(task["ansible.builtin.command"]["argv"])
    shell_index = argv.index("-c")
    assert argv[shell_index + 2:shell_index + 4] == ["--", "mautic-installer"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_php = root / "php"
        fake_php.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
        fake_php.chmod(0o755)
        command = argv[shell_index + 1].replace("/var/www/html", directory)
        result = subprocess.run(
            ["/bin/sh", "-c", command, *argv[shell_index + 3:]],
            env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]},
            capture_output=True, text=True, check=True,
        )
        assert json.loads(result.stdout) == [
            "bin/console", "mautic:install", "https://mautic.example.invalid",
            "--admin_username=drill-admin", "--admin_email=drill-admin@example.invalid",
            "--admin_password=" + password, "--force",
        ]
        assert not (root / "injected").exists()


def installation_finishes_migrations():
    names = [task.get("name") for task in kubernetes_tasks]
    install = names.index("Mautic Kubernetes | Run the non-interactive installer")
    migrate = names.index("Mautic Kubernetes | Finish the installed schema migrations")
    marker = names.index("Mautic Kubernetes | Record installation on the persistent config volume")
    converge = names.index("Mautic Kubernetes | Converge installed config")
    assert install < migrate < marker < converge
    task = kubernetes_tasks[migrate]
    assert task["when"] == "_mautic_k8s_install_state.stdout | trim == 'missing'"
    assert task["ansible.builtin.command"]["argv"][-1] == (
        "cd /var/www/html && exec php bin/console doctrine:migrations:migrate --no-interaction"
    )
    assert task["no_log"] is True
    report = task_named(kubernetes_tasks, "Mautic Kubernetes | Report a migration failure without credentials")
    assert report["vars"]["scrub_failure_result"] == "{{ _mautic_k8s_migrations_result }}"


def main():
    static_contract()
    rendered_workload()
    installer_arguments()
    installation_finishes_migrations()
    print("Mautic: Docker default, Kubernetes workload, database and shared recovery contracts passed")


if __name__ == "__main__":
    main()
