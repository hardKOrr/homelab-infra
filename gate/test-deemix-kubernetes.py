#!/usr/bin/env python3
"""Check Deemix's Docker-default and Kubernetes hosting contract."""

from pathlib import Path
import json
import subprocess
import tempfile

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/deemix.yml")["deemix_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    client = defaults["backup"]["client"]
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert client.get(key), f"backup.client.{key} missing"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/deemix.yml")}
    docker = plays["Deemix | Deploy"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("name") == "deemix"
        and task.get("when") == "hostvars['localhost'].app_config.hosting.method in ['docker', 'native']"
        for task in docker["pre_tasks"] + docker.get("tasks", [])
    )
    kube = plays["Deemix | Deploy on Kubernetes"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes"
        and task.get("when") == "app_config.hosting.method == 'kubernetes'"
        for task in kube.get("pre_tasks", []) + kube["tasks"]
    )
    prepare = plays["Deemix | Prepare Kubernetes"]["tasks"]
    assert any(
        task.get("ansible.builtin.include_role", {}).get("tasks_from") == "resolve-kubernetes-media"
        for task in prepare
    )
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")

    resolve = read_yaml("ansible/roles/deemix/tasks/resolve-kubernetes-media.yml")
    share = next(t for t in resolve if "resolve-media-nfs.yml" in str(t.get("ansible.builtin.include_tasks")))
    # The download directory is mounted at the Docker stack's fixed container path.
    assert share["vars"]["k8s_media_mounts"][0]["path"] == "/downloads"

    kubernetes = read_yaml("ansible/roles/deemix/tasks/kubernetes.yml")
    assert includes(kubernetes, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(kubernetes, "tasks/kubernetes/apply-manifest.yml")
    prepare_volumes = next(
        t for t in kubernetes if "prepare-media-volumes.yml" in str(t.get("ansible.builtin.include_tasks"))
    )["vars"]["k8s_media_shares"]
    assert "'mount_path': share.path" in prepare_volumes and "'read_only': false" in prepare_volumes
    backup = next(
        t for t in kubernetes if "tasks/kubernetes/recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks"))
    )
    claim = backup["vars"]["k8s_recovery"]["archives"][0]
    assert claim["name"] == "data" and claim["claim"] == "{{ instance }}-config"
    manifest = (REPO / "ansible/roles/deemix/templates/manifest.yaml.j2").read_text()
    assert "mountPath" in manifest and "DEEMIX_SINGLE_USER" in manifest

    # Exercise the in-container JSON edit: preserve canaries, types and ownership,
    # and report no change on its first repeat.
    settings = read_yaml("ansible/roles/deemix/tasks/kubernetes-settings.yml")[0]
    argv = settings["ansible.builtin.command"]["argv"]
    assert argv[-3:-1] == ["sh", "-ec"]
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.json"
        original = {"createSingleFolder": False, "tracknameTemplate": "canary", "number": 7}
        config.write_text(json.dumps(original))
        owner = config.stat().st_uid
        script = argv[-1].replace("/config/", directory + "/")
        for expected in ("changed", "unchanged"):
            result = subprocess.run(["sh", "-ec", script], check=True, capture_output=True, text=True)
            assert result.stdout.strip() == expected
            assert json.loads(config.read_text()) == {**original, "createSingleFolder": True}
            assert config.stat().st_uid == owner
        assert sorted(p.name for p in Path(directory).iterdir()) == ["config.json"]

    restore = read_yaml("ansible/roles/deemix/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml("ansible/roles/deemix/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"


if __name__ == "__main__":
    main()
