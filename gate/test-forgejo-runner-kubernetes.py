#!/usr/bin/env python3
"""Check Forgejo Runner's Docker default and its Kubernetes hosting contract."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ROLE = REPO / "ansible/roles/forgejo-runner"


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml(REPO / "ansible/vars/app-defaults/forgejo-runner.yml")["forgejo_runner_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["routing"] == {"proxy": "none", "access": "internal", "identity": "none"}

    plays = {play["name"]: play for play in read_yaml(REPO / "ansible/playbooks/apps/forgejo-runner.yml")}
    provision = plays["Forgejo Runner | Provision"]["tasks"]
    stack = next(t for t in provision if "find-or-create-host" in str(t.get("ansible.builtin.import_tasks")))
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    assert includes(plays["Forgejo Runner | Prepare Kubernetes"]["tasks"], "tasks/kubernetes/open-app-access.yml")
    deploy = plays["Forgejo Runner | Deploy"]["tasks"]
    assert all("hostvars['localhost'].app_config.hosting.method == 'docker'" in str(t.get("when")) for t in deploy)
    kube = plays["Forgejo Runner | Deploy on Kubernetes"]["tasks"][0]
    assert kube["ansible.builtin.include_role"]["tasks_from"] == "kubernetes"
    assert kube["when"] == "app_config.hosting.method == 'kubernetes'"

    tasks = read_yaml(ROLE / "tasks/kubernetes.yml")
    for shared in ("resolve-cluster", "ensure-namespace", "sync-secret", "apply-manifest", "recovery-backup"):
        assert includes(tasks, f"tasks/kubernetes/{shared}.yml"), shared
    assert includes(tasks, "registration.yml")
    backup = next(t for t in tasks if "recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert backup["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ instance }}-data"
    # Docker and Kubernetes share the registration steps.
    assert includes(read_yaml(ROLE / "tasks/main.yml"), "registration.yml")
    for name in ("files", "clear"):
        for method in ("host", "kubernetes"):
            assert (ROLE / f"tasks/registration-{name}-{method}.yml").is_file()

    manifest = (ROLE / "templates/manifest.yaml.j2").read_text()
    for needle in (
        "privileged: true",
        "kind: PersistentVolumeClaim",
        "tcp://localhost:2375",
        "secretKeyRef",
        "register-and-run.sh",
        "{{ _forgejo_runner_registration_fingerprint }}",
    ):
        assert needle in manifest, needle
    # No port, route or storageClassName: the cluster default class backs the volume.
    for absent in ("kind: Service", "kind: Ingress", "storageClassName"):
        assert absent not in manifest, absent
    print("Forgejo Runner Kubernetes contract passed.")


if __name__ == "__main__":
    main()
