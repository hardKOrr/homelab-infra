#!/usr/bin/env python3
"""Check Open WebUI deployment bootstrap and native recovery wiring.

Checks: Open WebUI's default Docker bootstrap, Kubernetes PVC/workload/Ingress, cluster
firewall inputs, and shared backup wiring.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def task_named(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def open_webui_release_image_and_docker_bootstrap():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/open-webui.yml").read_text(encoding="utf-8")
    )
    assert (
        defaults["open_webui_defaults"]["app"]["image"]
        == "ghcr.io/open-webui/open-webui:v0.11.4"
    )

    plays = yaml.safe_load(
        (ROOT / "ansible/playbooks/apps/open-webui.yml").read_text(encoding="utf-8")
    )
    deploy = next(play for play in plays if play.get("name") == "Open WebUI | Deploy")
    tasks = deploy["tasks"]

    bootstrap = task_named(tasks, "Bootstrap Docker guest")
    updates = task_named(tasks, "Configure OS update notifications")
    docker = task_named(tasks, "Ensure Docker is installed on the AI stack host")
    app = task_named(tasks, "Deploy Open WebUI")

    assert tasks.index(bootstrap) < tasks.index(updates) < tasks.index(docker) < tasks.index(app)
    assert docker["ansible.builtin.include_role"]["name"] == "docker"


def open_webui_kubernetes_deployment_uses_shared_operations():
    defaults = yaml.safe_load(
        (ROOT / "ansible/vars/app-defaults/open-webui.yml").read_text(encoding="utf-8")
    )["open_webui_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert defaults["app"]["container_port"] == 8080
    assert defaults["app"]["data_mount_path"] == "/app/backend/data"
    assert defaults["app"]["storage"] > 0
    assert defaults["backup"]["application_consistent"] is True

    plays = yaml.safe_load(
        (ROOT / "ansible/playbooks/apps/open-webui.yml").read_text(encoding="utf-8")
    )
    k8s_play = next(play for play in plays if play.get("name") == "Open WebUI | Deploy Kubernetes")
    deploy = task_named(k8s_play["tasks"], "Deploy Open WebUI on the Kubernetes hosting backend")
    assert deploy["ansible.builtin.include_role"]["tasks_from"] == "kubernetes"
    assert deploy["when"] == "hostvars['localhost'].app_config.hosting.method == 'kubernetes'"
    assert "localhost" in next(play for play in plays if play.get("name") == "Open WebUI | Deploy")["hosts"]

    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/open-webui/tasks/kubernetes.yml").read_text(encoding="utf-8")
    )
    cluster = task_named(tasks, "Open WebUI Kubernetes | Resolve the cluster and its API host")
    assert cluster["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/resolve-cluster.yml")
    firewall = task_named(tasks, "Open WebUI Kubernetes | Open app network access through the cluster alias")
    assert firewall["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/open-app-access.yml")
    apply = task_named(tasks, "Open WebUI Kubernetes | Apply the workload manifest")
    assert apply["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/apply-manifest.yml")
    backup = task_named(tasks, "Open WebUI Kubernetes | Configure the shared application backup")
    assert backup["ansible.builtin.include_tasks"].endswith("tasks/kubernetes/recovery-backup.yml")
    assert backup["vars"]["k8s_recovery"]["archives"] == [
        {"name": "data", "claim": "{{ instance }}-data"}
    ]
    assert backup["vars"]["k8s_recovery"]["stop"] == ["{{ instance }}"]

    manifest = (
        ROOT / "ansible/roles/open-webui/templates/kubernetes-manifest.yaml.j2"
    ).read_text(encoding="utf-8")
    assert "kind: PersistentVolumeClaim" in manifest
    assert "storageClassName:" not in manifest
    assert "mountPath: {{ app_config.app.data_mount_path }}" in manifest
    assert "containerPort: {{ app_config.app.container_port }}" in manifest
    assert "path: /health" in manifest
    assert "kind: Ingress" in manifest
    assert "name: {{ instance }}-key" in manifest

    # This route must permit the pod's node address to reach the selected Ollama endpoint.
    assert "upstream-ollama" in tasks[2]["ansible.builtin.set_fact"]["_open_webui_k8s_firewall_egress"]
    assert "urlsplit" in tasks[2]["vars"]["_open_webui_ollama_host"]


def open_webui_recovery_uploads_through_the_shared_client():
    script = (ROOT / "ansible/roles/open-webui/files/open-webui-recovery").read_text(
        encoding="utf-8"
    )
    config = (
        ROOT / "ansible/roles/open-webui/templates/open-webui-recovery.env.j2"
    ).read_text(encoding="utf-8")
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/open-webui/tasks/main.yml").read_text(encoding="utf-8")
    )

    # PBS credentials live only in the shared client's root-only settings.
    assert "PBS_CLIENT_CONFIG=" in config and "PBS_PASSWORD" not in config
    assert '"$PBS_CLIENT_SCRIPT" backup --id "$OPEN_WEBUI_BACKUP_ID" --no-prune data.pxar:' in script
    assert "PRAGMA integrity_check" in script
    install = task_named(tasks, "Open WebUI | Install the shared PBS client and this instance's settings")
    assert install["ansible.builtin.include_tasks"].endswith("tasks/backup/pbs-client-script.yml")
    assert install["vars"]["pbs_client_runtime"] == "docker"
    render = task_named(tasks, "Open WebUI | Render root-only recovery configuration")
    assert tasks.index(install) < tasks.index(render)


if __name__ == "__main__":
    open_webui_release_image_and_docker_bootstrap()
    open_webui_kubernetes_deployment_uses_shared_operations()
    open_webui_recovery_uploads_through_the_shared_client()
    print("ok Open WebUI Docker and Kubernetes deployment and shared recovery wiring")
