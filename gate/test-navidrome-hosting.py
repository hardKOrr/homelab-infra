#!/usr/bin/env python3
"""Check Navidrome's Docker-default and Kubernetes hosting contract."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def main():
    defaults = read_yaml("ansible/vars/app-defaults/navidrome.yml")["navidrome_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert defaults["hosting"]["supported"] == ["docker-vm", "kubernetes"]
    assert defaults["hosting"]["impossible"] == {}

    playbook = read_yaml("ansible/playbooks/apps/navidrome.yml")
    docker_hosts = [play for play in playbook if play["name"] == "Navidrome | Deploy"]
    assert len(docker_hosts) == 1
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "navidrome"
        and task.get("when") == "hostvars['localhost'].app_config.hosting.method == 'docker'"
        for task in docker_hosts[0]["tasks"]
    )
    assert any(
        play["name"] == "Navidrome | Deploy on Kubernetes"
        and any(
            task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
            and task.get("when") == "app_config.hosting.method == 'kubernetes'"
            for task in play["tasks"]
        )
        for play in playbook
    )
    assert "tasks/kubernetes/open-app-access.yml" in str(playbook)

    kubernetes_tasks = read_yaml("ansible/roles/navidrome/tasks/kubernetes.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in kubernetes_tasks]
    assert "../../../tasks/kubernetes/apply-manifest.yml" in includes
    assert "../../../tasks/kubernetes/recovery-backup.yml" in includes
    assert not any("recovery" in include and "kubernetes/" not in include for include in includes)

    manifest = (REPO / "ansible/roles/navidrome/templates/manifest.yaml.j2").read_text()
    assert "name: {{ instance }}-data" in manifest
    assert "storageClassName:" not in manifest
    assert "hostPath:" in manifest
    assert "path: {{ _navidrome_music_path | to_json }}" in manifest
    assert "mountPath: /music\n              readOnly: true" in manifest
    assert "runAsUser: {{ _navidrome_uid }}" in manifest
    assert "ND_DATAFOLDER" in manifest and "ND_MUSICFOLDER" in manifest
    assert "kind: Ingress" in manifest

    restore = read_yaml("ansible/roles/navidrome/tasks/restore-kubernetes.yml")
    assert any(
        task.get("ansible.builtin.include_tasks") == "../../../tasks/kubernetes/recovery-restore.yml"
        and task.get("vars", {}).get("k8s_recovery", {}).get("archives") == [
            {
                "name": "data",
                "claim": "{{ restore_target }}-data",
                "owner": "{{ restore_target_config.app.puid | default(1313) }}:{{ restore_target_config.app.pgid | default(1313) }}",
            }
        ]
        for task in restore
    )

    wrapper = read_yaml("ansible/roles/navidrome/tasks/restore.yml")
    assert len(wrapper) == 3
    assert "restore-kubernetes.yml" in wrapper[0]["ansible.builtin.include_tasks"]
    assert "restore-docker.yml" in wrapper[1]["ansible.builtin.include_tasks"]
    print("Navidrome hosting: Docker default and shared Kubernetes recovery passed")


if __name__ == "__main__":
    main()
