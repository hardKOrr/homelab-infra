#!/usr/bin/env python3
"""Check the Servarr apps' Docker-default and Kubernetes hosting contract (servarr role)."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def check(app):
    defaults = read_yaml(f"ansible/vars/app-defaults/{app}.yml")[f"{app}_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["app"]["storage"] == 2
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert defaults["backup"]["client"].get(key), f"backup.client.{key} missing"

    # Each app's playbook is a deploy.yml shim; the servarr role's hooks carry its storage
    # (deploy-prepare.yml) and its media registry entry (deploy-record.yml).
    [shim] = read_yaml(f"ansible/playbooks/apps/{app}.yml")
    assert shim["ansible.builtin.import_playbook"] == "deploy.yml" and shim["vars"]["app"] == app
    assert defaults["deploy"]["role"] == "servarr"
    assert defaults["platform_layers"] == ["media_storage"]
    assert defaults["routing"]["monitor_path"] == "/ping"


def roles():
    media = read_yaml("ansible/roles/servarr/tasks/kubernetes-media.yml")
    assert includes(media, "tasks/kubernetes/resolve-media-nfs.yml")
    # The named storage owner may not exist on the node yet: directories use numeric ids.
    mkdir = next(t for t in media if "ansible.builtin.file" in t)["ansible.builtin.file"]
    assert ").uid" in mkdir["owner"] and ").gid" in mkdir["group"]
    assert ".user" not in mkdir["owner"] + mkdir["group"]

    tasks = read_yaml("ansible/roles/servarr/tasks/kubernetes-workload.yml")
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
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml("ansible/roles/servarr/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    # Docker keeps reading config.xml; Kubernetes never does, and calls the app via ingress.
    verify = read_yaml("ansible/roles/servarr/tasks/main/verify.yml")
    slurp = next(t for t in verify if "ansible.builtin.slurp" in t)
    assert slurp["when"] == "app_config.hosting.method in ['docker', 'native']"
    endpoint = (REPO / "ansible/roles/servarr/tasks/main/endpoint.yml").read_text()
    assert "ingress_vip" in endpoint and "'Host': servarr_fqdn" in endpoint

    prepare = read_yaml("ansible/roles/servarr/tasks/deploy-prepare.yml")
    k8s = next(t for t in prepare if "kubernetes-media.yml" in str(t))
    assert k8s["when"] == "app_config.hosting.method == 'kubernetes'"
    mounts = next(t for t in prepare if "deploy_host_mounts" in str(t))
    assert mounts["when"] == "app_config.hosting.method in ['docker', 'native']"
    record = read_yaml("ansible/roles/servarr/tasks/deploy-record.yml")[0]
    assert record["when"] == "app_config.hosting.method in ['docker', 'native']"
    assert record["vars"]["generated_facts_service"] == "media"
    entry = read_yaml("ansible/roles/servarr/tasks/main.yml")
    assert any(t.get("ansible.builtin.include_tasks") == "kubernetes-workload.yml"
               and t.get("when") == "app_config.hosting.method == 'kubernetes'" for t in entry)
    assert "main.yml" in str(read_yaml("ansible/roles/servarr/tasks/kubernetes.yml"))


if __name__ == "__main__":
    roles()
    for name in ("sonarr", "radarr", "lidarr", "prowlarr"):
        check(name)
