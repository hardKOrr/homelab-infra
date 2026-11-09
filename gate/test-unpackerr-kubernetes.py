#!/usr/bin/env python3
"""Check Unpackerr's Docker-default and Kubernetes hosting contract."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
ROLE = "ansible/roles/unpackerr"


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/unpackerr.yml")["unpackerr_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes"]
    assert defaults["routing"]["proxy"] == "none"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/unpackerr.yml")}
    provision = plays["Unpackerr | Provision"]["tasks"]
    stack = next(t for t in provision if "find-or-create-host" in str(t))
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    attach = next(t for t in provision if "attach-host-mounts" in str(t))
    assert "app_config.hosting.method == 'docker'" in attach["when"]
    prepare = plays["Unpackerr | Prepare Kubernetes"]["tasks"]
    assert includes(prepare, "tasks/kubernetes/resolve-media-nfs.yml")
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")
    deploy = plays["Unpackerr | Deploy"]["tasks"]
    assert all(t.get("when") == "hostvars['localhost'].app_config.hosting.method == 'docker'"
               for t in deploy if "include_role" in str(t))
    kube = plays["Unpackerr | Deploy on Kubernetes"]["tasks"]
    assert any(
        t.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
        and t.get("when") == "app_config.hosting.method == 'kubernetes'"
        for t in kube
    )

    # The role must not pull Docker onto a Kubernetes run through role dependencies.
    assert "dependencies" not in read_yaml(f"{ROLE}/meta/main.yml")

    tasks = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert includes(tasks, "arrs.yml")
    assert includes(tasks, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(tasks, "tasks/kubernetes/sync-secret.yml")
    assert includes(tasks, "tasks/kubernetes/apply-manifest.yml")
    shares = next(
        t for t in tasks if "prepare-media-volumes.yml" in str(t.get("ansible.builtin.include_tasks"))
    )["vars"]["k8s_media_shares"]
    assert "'mount_path': share.path" in shares and "'read_only': false" in shares

    manifest = (REPO / f"{ROLE}/templates/manifest.yaml.j2").read_text()
    assert "secretKeyRef" in manifest and "mountPath" in manifest
    # Stateless: no volume claim, service or ingress.
    for kind in ("PersistentVolumeClaim", "kind: Service", "kind: Ingress"):
        assert kind not in manifest, kind

    main_tasks = read_yaml(f"{ROLE}/tasks/main.yml")
    assert includes(main_tasks, "arrs.yml")


if __name__ == "__main__":
    main()
