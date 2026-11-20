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
    assert defaults["hosting"]["supported"] == ["docker-vm", "docker-lxc", "kubernetes", "native-lxc", "native-vm"]
    assert defaults["routing"]["proxy"] == "none"

    # Deploy runs through playbooks/apps/deploy.yml (gate/app-matrix.yml); the role's
    # deploy-prepare hook resolves the Kubernetes shares.
    prepare = read_yaml(f"{ROLE}/tasks/deploy-prepare.yml")
    assert includes(prepare, "tasks/kubernetes/resolve-media-nfs.yml")

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
