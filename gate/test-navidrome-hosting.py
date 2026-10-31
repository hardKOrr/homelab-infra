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
    assert any(
        task.get("ansible.builtin.include_tasks")
        == "../../roles/navidrome/tasks/resolve-kubernetes-library.yml"
        for play in playbook
        if play["name"] == "Navidrome | Prepare Kubernetes"
        for task in play["tasks"]
    )
    library = read_yaml("ansible/roles/navidrome/tasks/resolve-kubernetes-library.yml")
    assert any(
        task.get("ansible.builtin.command", {}).get("argv", [None])[0] == "findmnt"
        and task.get("delegate_to") == "{{ homelabinfra_config.proxmox.node }}"
        and task.get("no_log") is True
        for task in library
    )
    assert any(
        task.get("ansible.builtin.set_fact", {}).get("app_config")
        and task.get("no_log") is True
        and "navidrome-music-library" in str(task.get("vars", {}))
        for task in library
    )

    kubernetes_tasks = read_yaml("ansible/roles/navidrome/tasks/kubernetes.yml")
    includes = [task.get("ansible.builtin.include_tasks", "") for task in kubernetes_tasks]
    assert "../../../tasks/kubernetes/apply-manifest.yml" in includes
    assert "../../../tasks/kubernetes/recovery-backup.yml" in includes
    assert "verify-kubernetes-library.yml" in includes
    assert not any("recovery" in include and "kubernetes/" not in include for include in includes)

    nfs_probe = read_yaml("ansible/roles/navidrome/tasks/verify-kubernetes-library.yml")
    assert any(
        task.get("ansible.builtin.command", {}).get("argv", [])[:4]
        == ["timeout", "--kill-after=2s", "20s", "mount"]
        and task.get("no_log") is True
        for task in nfs_probe[0]["block"]
    )
    probe_diagnostic = next(
        task
        for task in nfs_probe[0]["block"]
        if task.get("name") == "Report an NFS probe failure without private endpoints"
    )
    assert "_navidrome_nfs_server_ip" in str(probe_diagnostic)
    assert "_navidrome_nfs_path" in str(probe_diagnostic)
    assert "<NFS server>" in str(probe_diagnostic)
    assert "<music export>" in str(probe_diagnostic)
    assert nfs_probe[0]["always"][-1].get("ansible.builtin.file", {}).get("state") == "absent"

    manifest = (REPO / "ansible/roles/navidrome/templates/manifest.yaml.j2").read_text()
    assert "name: {{ instance }}-data" in manifest
    assert "storageClassName:" not in manifest
    assert "nfs:" in manifest
    assert "server: {{ _navidrome_nfs_server_ip | to_json }}" in manifest
    assert "path: {{ _navidrome_nfs_path | to_json }}" in manifest
    assert "readOnly: true" in manifest
    assert "hostPath:" not in manifest
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
