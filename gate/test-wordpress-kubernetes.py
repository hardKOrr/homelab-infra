#!/usr/bin/env python3
"""Check WordPress's Kubernetes hosting and shared recovery wiring.

Checks: WordPress keeps Docker as its default, dispatches Deploy, Backup and Restore by
`hosting.method`, and renders its default-class content claim, WP-CLI jobs and shared,
database-consistent Kubernetes recovery inputs.
"""

import os
import subprocess
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]
os.environ["ANSIBLE_CONFIG"] = str(ROOT / "ansible/ansible.cfg")
ROLE = ROOT / "ansible/roles/wordpress"


def render(path, variables):
    text = (ROOT / path).read_text(encoding="utf-8")
    return Templar(loader=DataLoader(), variables=variables).template(text)


def tasks(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def fixture():
    app_config = {"app": {
        "image": "wordpress:6.9.4-php8.3-apache",
        "cli_image": "wordpress:cli-2.12.0-php8.3",
        "container_port": 80,
        "storage": 10,
        "default_theme": "twentytwentyfive",
        "admin_user": "admin",
        "admin_email": "admin@example.test",
        "admin_name": "Homelab Admin",
        "site_title": "Homelab WordPress",
    }}
    return {
        "instance": "wordpress",
        "wordpress_k8s_instance": "wordpress",
        "wordpress_k8s_config": app_config,
        "wordpress_fqdn": "wordpress.example.test",
        "wordpress_k8s_content_claim": "wordpress-content",
        "wordpress_k8s_admin_secret": "wordpress-admin",
        "wordpress_k8s_admin_marker": "/var/www/html/wp-content/.homelab-infra-admin",
        "app_config": app_config,
    }


def check_defaults_and_dispatch():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/wordpress.yml").read_text(encoding="utf-8")
    )["wordpress_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm"]
    assert defaults["hosting"]["impossible"] == {}
    assert defaults["backup"]["client"]["image"] == "python:3.12-bookworm"

    deploy = tasks("ansible/playbooks/apps/wordpress.yml")
    validate = next(
        task for task in deploy[0]["pre_tasks"]
        if task.get("name") == "Validate supported hosting method"
    )
    provision = next(task for task in deploy[0]["tasks"]
                     if task.get("name") == "Provision the named MariaDB database and role")
    stack = next(task for task in deploy[0]["tasks"]
                 if task.get("name") == "Find or create services stack host")
    assert deploy[0]["pre_tasks"].index(validate) < len(deploy[0]["pre_tasks"])
    assert provision["ansible.builtin.include_tasks"].endswith("database/provision.yml")
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    k8s = next(play for play in deploy if play.get("name") == "WordPress | Deploy on Kubernetes")
    assert k8s["tasks"][0]["ansible.builtin.include_role"] == {"name": "wordpress", "tasks_from": "kubernetes"}
    assert k8s["tasks"][0]["when"] == "hostvars['localhost'].app_config.hosting.method == 'kubernetes'"
    wire = deploy[-1]
    assert "homelabinfra_infra.kubernetes.ingress_vip" in wire["vars"]["wiring_upstream_host"]
    assert "80 if app_config.hosting.method == 'kubernetes'" in wire["vars"]["wiring_upstream_port"]
    k8s_record = next(task for task in wire["tasks"]
                      if task.get("name") == "Record WordPress on each Kubernetes node")
    assert k8s_record["when"] == "app_config.hosting.method == 'kubernetes'"

    # Backup App and Restore App own WordPress's recovery on every method; the role's
    # backup.yml and restore.yml dispatch by hosting method.
    assert defaults["recovery"] == {"methods": ["native"], "native": {
        "backup_playbook": "backup-app.yml", "restore_playbook": "restore-app.yml"}}
    restore = tasks("ansible/roles/wordpress/tasks/restore.yml")
    assert [task["ansible.builtin.include_tasks"] for task in restore] == [
        "restore-kubernetes.yml", "restore-guest.yml"]
    assert "== 'kubernetes'" in restore[0]["when"]
    backup = tasks("ansible/roles/wordpress/tasks/backup.yml")
    assert backup[0]["when"] == "not (wordpress_native | bool)"
    assert backup[0]["block"][0]["ansible.builtin.command"]["argv"] == [
        "{{ wordpress_recovery_script }}", "--backup"]
    assert backup[1]["ansible.builtin.include_tasks"] == "backup-native.yml"
    inputs = tasks("ansible/roles/wordpress/tasks/restore-inputs.yml")
    assert "restore_target_config.routing.subdomain" in inputs[1]["ansible.builtin.set_fact"]["restore_target_fqdn"]
    seam = next(play for play in tasks("ansible/playbooks/maintenance/restore-app.yml")
                if play["name"].startswith("Restore App | Restore Docker"))["tasks"][0]["vars"]
    assert seam["restore_target_fqdn"] == "{{ hostvars['localhost'].restore_target_fqdn | default('') }}"


def check_workload_and_cli_jobs():
    variables = fixture()
    manifest = list(yaml.safe_load_all(render(
        "ansible/roles/wordpress/templates/manifest.yaml.j2", variables
    )))
    claim = next(doc for doc in manifest if doc["kind"] == "PersistentVolumeClaim")
    assert claim["metadata"]["name"] == "wordpress-content"
    assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert "storageClassName" not in claim["spec"]

    deployment = next(doc for doc in manifest if doc["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert len(pod["containers"]) == 1
    assert {mount["mountPath"] for mount in pod["containers"][0]["volumeMounts"]} == {
        "/var/www/html/wp-content"
    }
    assert pod["initContainers"][0]["name"] == "seed-default-theme"
    assert "storageClassName" not in str(manifest)
    service = next(doc for doc in manifest if doc["kind"] == "Service")
    assert service["spec"]["ports"][0]["port"] == 80
    ingress = next(doc for doc in manifest if doc["kind"] == "Ingress")
    assert ingress["spec"]["rules"][0]["host"] == "wordpress.example.test"

    for mode in ("detect", "initialize", "restore"):
        job_vars = {
            **variables,
            "wordpress_k8s_cli_mode": mode,
            "_wordpress_k8s_install_state": "uninstalled",
        }
        job = yaml.safe_load(render(
            "ansible/roles/wordpress/templates/cli-job.yaml.j2", job_vars
        ))
        pod = job["spec"]["template"]["spec"]
        init = pod["initContainers"][0]
        cli = pod["containers"][0]
        assert init["image"] == variables["app_config"]["app"]["image"]
        assert init["args"] == ["/bin/true"]
        assert cli["image"] == variables["app_config"]["app"]["cli_image"]
        assert cli["securityContext"]["runAsUser"] == 33
        assert {mount["mountPath"] for mount in cli["volumeMounts"]} == {
            "/var/www/html", "/var/www/html/wp-content"
        }
        assert pod["volumes"][1]["persistentVolumeClaim"]["claimName"] == "wordpress-content"
        script = cli["args"][0]
        assert "--path=/var/www/html" in script
        shell_check = subprocess.run(
            ["sh", "-n"], input=script, text=True, capture_output=True, check=False
        )
        assert shell_check.returncode == 0, shell_check.stderr
        expected_secrets = ["wordpress-env"]
        if mode != "detect":
            expected_secrets.append("wordpress-admin")
        assert [ref["secretRef"]["name"] for ref in cli["envFrom"]] == expected_secrets
        if mode == "detect":
            assert "WORDPRESS_INSTALL_STATE=installed" in script
            assert 'admin_marker="/var/www/html/wp-content/.homelab-infra-admin"' in script
            assert "marker_user=\"$(sed -n '1p' \"$admin_marker\")\"" in script
            assert 'exit 41' in script
            assert 'exit 42' in script
            assert 'exit 43' in script
            assert "marker_email" in script
        elif mode == "initialize":
            assert "core install" in script
            assert "theme activate" in script
            assert "theme install" not in script
            assert ".homelab-infra-admin" in script
            assert "wp_authenticate" in script
            assert "get_error_codes" in script
            assert "WORDPRESS_ADMIN_PASSWORD" in script
            assert script.index('if [ "$login"') < script.index("user update")
            assert script.index("user update") < script.index("wp_authenticate")
        else:
            assert 'option update home "$WORDPRESS_SITE_URL"' in script
            assert 'option update siteurl "$WORDPRESS_SITE_URL"' in script
            assert ".homelab-infra-admin" in script
            assert "wp_authenticate" in script
            assert "get_error_codes" in script


def check_shared_recovery_inputs():
    main = tasks("ansible/roles/wordpress/tasks/kubernetes.yml")
    main_text = str(main)
    for shared in (
        "tasks/kubernetes/apply-manifest.yml",
        "tasks/database/kubernetes-recovery-step.yml",
        "tasks/kubernetes/recovery-backup.yml",
    ):
        assert shared in main_text
    detect = next(task for task in main
                  if task.get("name") == "WordPress Kubernetes | Detect the database install and administrator continuity")
    detect_names = [task.get("name") for task in detect["block"]]
    assert "WordPress Kubernetes | Run the read-only install check" in detect_names
    assert "WordPress Kubernetes | Record the detected database and marker state" in detect_names
    assert "WordPress Kubernetes | Require the recorded login for an existing site" in detect_names
    assert "k8s_job_log" not in str(detect)
    assert "kubectl" not in main_text
    initialize = next(task for task in main
                      if task.get("name") == "WordPress Kubernetes | Initialize the administrator and record continuity")
    assert initialize["when"] == "not (_wordpress_k8s_admin_marker_present | bool)"
    cli_runner = tasks("ansible/roles/wordpress/tasks/kubernetes-cli-job.yml")
    job_vars = cli_runner[1]["vars"]
    assert job_vars["k8s_job_expected_exit_codes"] == "{{ [41, 42] if wordpress_k8s_cli_mode == 'detect' else [] }}"
    assert job_vars["k8s_job_report_changed"] == "{{ wordpress_k8s_cli_mode != 'detect' }}"

    shared_runner = tasks("ansible/tasks/kubernetes/run-job.yml")
    runner_names = [task.get("name") for task in shared_runner]
    for name in (
        "k8s job | Create the job from its manifest",
        "k8s job | Create the job from its cronjob",
        "k8s job | Delete the job now that it succeeded or returned an expected code",
    ):
        task = next(task for task in shared_runner if task.get("name") == name)
        assert task["changed_when"] == "k8s_job_report_changed | default(true) | bool"
    wait_index = runner_names.index("k8s job | Wait for the job to finish")
    exit_code_index = runner_names.index("k8s job | Read the completed main container exit code")
    expected_index = runner_names.index("k8s job | Record whether the exit code was expected")
    assert wait_index < exit_code_index < expected_index
    cleanup = next(task for task in shared_runner
                   if task.get("name") == "k8s job | Delete the job now that it succeeded or returned an expected code")
    assert "k8s_job_expected_exit" in cleanup["when"]
    fail = next(task for task in shared_runner if task.get("name") == "k8s job | Fail with what the job said")
    assert "not (k8s_job_expected_exit | bool)" in fail["when"]
    backup = next(task for task in main if "recovery-backup.yml" in str(task))
    recovery = backup["vars"]["k8s_recovery"]
    assert recovery["archives"] == [{
        "name": "content", "claim": "{{ wordpress_k8s_content_claim }}", "owner": "33:33"
    }]
    assert recovery["stop"] == ["{{ instance }}"]
    assert "k8s_database_step" in recovery["database"]

    restore = tasks("ansible/roles/wordpress/tasks/restore-kubernetes.yml")
    restore_text = str(restore)
    assert "tasks/database/kubernetes-recovery-step.yml" in restore_text
    assert "tasks/kubernetes/recovery-restore.yml" in restore_text
    safety = next(task for task in restore
                  if "require-recent-pre-restore-point.yml" in str(task))
    assert safety["when"] == ["restore_overwrite | bool", "restore_destination == 'existing'"], \
        "a new target has no earlier backup to require"
    assert "restore_target_config.backup.backup_id" in str(safety["vars"])
    shared_restore = next(task for task in restore
                          if "recovery-restore.yml" in str(task))
    assert restore.index(safety) < restore.index(shared_restore)
    assert "restore-kubernetes-rekey.yml" in restore_text
    # The re-key acts on the restore target, which differs from `instance` (the source)
    # when a point is restored into another WordPress.
    rekey = tasks("ansible/roles/wordpress/tasks/restore-kubernetes-rekey.yml")[0]["vars"]
    assert rekey["wordpress_k8s_instance"] == "{{ restore_target }}"
    assert rekey["wordpress_k8s_config"] == "{{ restore_target_config }}"
    target = {**fixture(), "wordpress_k8s_cli_mode": "restore", "wordpress_k8s_instance": "wordpress-copy",
              "wordpress_k8s_admin_secret": "wordpress-copy-admin", "_wordpress_k8s_install_state": "installed"}
    job = yaml.safe_load(render("ansible/roles/wordpress/templates/cli-job.yaml.j2", target))
    assert job["metadata"]["name"] == "wordpress-copy-wordpress-restore"
    assert "wordpress-copy-env" in str(job) and "'wordpress-env'" not in str(job)
    assert not (ROLE / "templates/backup-cronjob.yaml.j2").exists()
    assert not (ROLE / "templates/restore-job.yaml.j2").exists()


def main():
    check_defaults_and_dispatch()
    check_workload_and_cli_jobs()
    check_shared_recovery_inputs()
    print("WordPress Kubernetes: hosting dispatch, workload and shared recovery contract passed")


if __name__ == "__main__":
    main()
