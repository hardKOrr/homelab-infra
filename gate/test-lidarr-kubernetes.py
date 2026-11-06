#!/usr/bin/env python3
"""Check Lidarr's Docker-default and Kubernetes hosting contract (servarr role)."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/lidarr.yml")["lidarr_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["app"]["storage"] == 2
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert defaults["backup"]["client"].get(key), f"backup.client.{key} missing"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/lidarr.yml")}
    # The ingress hostname is derived only after the app's estate is resolved.
    pre = [str(t) for t in plays["Lidarr | Provision"]["pre_tasks"]]
    assert next(i for i, t in enumerate(pre) if "resolve-estate.yml" in t) < next(
        i for i, t in enumerate(pre) if "servarr_fqdn" in t)
    assert "routing.estate" in pre[next(i for i, t in enumerate(pre) if "resolve-estate.yml" in t)]
    # Docker stays the default: the stack host is only provisioned for that method.
    provision = plays["Lidarr | Provision"]["tasks"]
    assert all(task.get("when") for task in provision if "find-or-create-host" in str(task))
    prepare = plays["Lidarr | Prepare Kubernetes"]["tasks"]
    assert any(t.get("ansible.builtin.include_role", {}).get("tasks_from") == "kubernetes-media"
               for t in prepare)
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")
    kube = plays["Lidarr | Deploy on Kubernetes"]["tasks"]
    assert any(t.get("ansible.builtin.include_role", {}).get("name") == "servarr"
               and t.get("when") == "app_config.hosting.method == 'kubernetes'" for t in kube)
    entry = read_yaml("ansible/roles/servarr/tasks/main.yml")
    assert any(t.get("ansible.builtin.include_tasks") == "kubernetes.yml"
               and t.get("when") == "app_config.hosting.method == 'kubernetes'" for t in entry)
    assert plays["Lidarr | Deploy"]["hosts"].startswith("deploy_")

    media = read_yaml("ansible/roles/servarr/tasks/kubernetes-media.yml")
    assert includes(media, "tasks/kubernetes/resolve-media-nfs.yml")
    # The named storage owner may not exist on the node yet: directories use numeric ids.
    mkdir = next(t for t in media if "ansible.builtin.file" in t)["ansible.builtin.file"]
    assert ").uid" in mkdir["owner"] and ").gid" in mkdir["group"]
    assert ".user" not in mkdir["owner"] + mkdir["group"]

    tasks = read_yaml("ansible/roles/servarr/tasks/kubernetes.yml")
    assert includes(tasks, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(tasks, "tasks/kubernetes/apply-manifest.yml")
    assert includes(tasks, "tasks/kubernetes/sync-secret.yml")
    shares = next(t for t in tasks if "prepare-media-volumes.yml" in str(t.get("ansible.builtin.include_tasks")))
    assert "'mount_path': share.path" in shares["vars"]["k8s_media_shares"]
    assert "'read_only': false" in shares["vars"]["k8s_media_shares"]
    backup = next(t for t in tasks if "recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    archive = backup["vars"]["k8s_recovery"]["archives"][0]
    assert archive["name"] == "data" and archive["claim"] == "{{ instance }}-config"
    # The key is stored, then synced, before the workload that reads it is applied.
    order = [str(t.get("ansible.builtin.include_tasks")) for t in tasks]
    assert (next(i for i, o in enumerate(order) if "upsert-item" in o)
            < next(i for i, o in enumerate(order) if "sync-secret" in o)
            < next(i for i, o in enumerate(order) if "apply-manifest" in o))

    manifest = (REPO / "ansible/roles/servarr/templates/manifest.yaml.j2").read_text()
    assert "k8s_media_volume_mounts" in manifest and "secretKeyRef" in manifest
    assert "__AUTH__APIKEY" in manifest and "kind: Ingress" in manifest

    restore = read_yaml("ansible/roles/servarr/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-docker.yml")
    job = read_yaml("ansible/roles/servarr/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    # Docker keeps reading config.xml; Kubernetes never does, and calls the app via ingress.
    verify = read_yaml("ansible/roles/servarr/tasks/main/verify.yml")
    slurp = next(t for t in verify if "ansible.builtin.slurp" in t)
    assert slurp["when"] == "app_config.hosting.method == 'docker'"
    endpoint = (REPO / "ansible/roles/servarr/tasks/main/endpoint.yml").read_text()
    assert "ingress_vip" in endpoint and "'Host': servarr_fqdn" in endpoint

    wiring = (REPO / "ansible/playbooks/apps/lidarr.yml").read_text()
    assert "ingress_vip" in wiring and "not (_lidarr_k8s | bool)" in wiring


if __name__ == "__main__":
    main()
