#!/usr/bin/env python3
"""Check Bazarr's Docker-default and Kubernetes hosting contract."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/bazarr.yml")["bazarr_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]

    playbook = read_yaml("ansible/playbooks/apps/bazarr.yml")
    plays = {play["name"]: play for play in playbook}
    docker = plays["Bazarr | Deploy"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "bazarr"
        and task.get("when") == "hostvars['localhost'].app_config.hosting.method == 'docker'"
        for task in docker["tasks"]
    )
    kube = plays["Bazarr | Deploy on Kubernetes"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
        and task.get("when") == "app_config.hosting.method == 'kubernetes'"
        for task in kube["tasks"]
    )
    prepare = plays["Bazarr | Prepare Kubernetes"]["tasks"]
    assert includes(prepare, "tasks/kubernetes/resolve-media-nfs.yml")
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")

    kubernetes = read_yaml("ansible/roles/bazarr/tasks/kubernetes.yml")
    assert includes(kubernetes, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(kubernetes, "tasks/kubernetes/apply-manifest.yml")
    backup = next(
        task for task in kubernetes
        if "tasks/kubernetes/recovery-backup.yml" in str(task.get("ansible.builtin.include_tasks"))
    )
    claim = backup["vars"]["k8s_recovery"]["archives"][0]
    assert claim["name"] == "data" and claim["claim"] == "{{ instance }}-config"
    assert "mountPath" in (REPO / "ansible/roles/bazarr/templates/manifest.yaml.j2").read_text()
    # Shares are mounted writable (Bazarr writes subtitles) at their Docker container path.
    shares = next(
        task for task in kubernetes
        if "prepare-media-volumes.yml" in str(task.get("ansible.builtin.include_tasks"))
    )["vars"]["k8s_media_shares"]
    assert "'mount_path': share.path" in shares and "'read_only': false" in shares

    restore = read_yaml("ansible/roles/bazarr/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-docker.yml")
    shared = read_yaml("ansible/roles/bazarr/tasks/restore-kubernetes.yml")
    restore_job = shared[0]
    assert "tasks/kubernetes/recovery-restore.yml" in restore_job["ansible.builtin.include_tasks"]
    assert restore_job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"


if __name__ == "__main__":
    main()
