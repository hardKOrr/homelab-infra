#!/usr/bin/env python3
"""Check Tautulli's Docker-default and Kubernetes hosting contract."""

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
ROLE = "ansible/roles/tautulli"


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def main():
    defaults = read_yaml("ansible/vars/app-defaults/tautulli.yml")["tautulli_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["app"]["storage"] == 2
    assert defaults["firewall"]["egress"][0]["name"] == "plex"
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert defaults["backup"]["client"].get(key), f"backup.client.{key} missing"

    # Kubernetes only: a Docker or native guest reaches Plex on the lab network.
    assert defaults["firewall"]["methods"] == ["kubernetes"]
    # Plex's endpoint is resolved in the deploy-prepare hook, which deploy.yml runs before
    # the cluster firewall step that needs it (gate/app-matrix.yml records that order).
    prepare = read_yaml(f"{ROLE}/tasks/deploy-prepare.yml")
    assert prepare[0]["ansible.builtin.include_tasks"] == "plex.yml"
    assert prepare[0]["when"] == "app_config.hosting.method == 'kubernetes'"

    tasks = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert includes(tasks, "tasks/kubernetes/single-container.yml")
    assert includes(tasks, "tasks/kubernetes/sync-secret.yml")
    # No media shares: Tautulli's Docker stack mounts none.
    assert not includes(tasks, "prepare-media-volumes.yml")
    order = [str(t.get("ansible.builtin.include_tasks")) for t in tasks]
    assert (next(i for i, o in enumerate(order) if "upsert-item" in o)
            < next(i for i, o in enumerate(order) if "sync-secret" in o)
            < next(i for i, o in enumerate(order) if "single-container" in o))
    backup = next(t for t in tasks if "recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    archive = backup["vars"]["k8s_recovery"]["archives"][0]
    assert archive["name"] == "data" and archive["claim"] == "{{ instance }}-config"

    workload = next(t for t in tasks if "single-container.yml" in str(t.get("ansible.builtin.include_tasks")))
    app = workload["vars"]["k8s_app"]
    assert app["host"] == "{{ tautulli_fqdn }}"
    assert app["volumes"] == [{"name": "config", "mount_path": "/config",
                               "storage": "{{ app_config.app.storage | default(2) }}"}]
    secrets = {e["name"]: e["secret"] for e in app["env"] if "secret" in e}
    assert secrets == {"TAUTULLI_PMS_TOKEN": "plex-token", "TAUTULLI_API_KEY": "api-key"}
    # A rotated credential must roll the pod: env from a Secret is read at start.
    checksum = app["pod_annotations"]["checksum/credentials"]
    assert "_tautulli_plex_token" in checksum and "_tautulli_api_key" in checksum

    restore = read_yaml(f"{ROLE}/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml(f"{ROLE}/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    verify = (REPO / f"{ROLE}/tasks/verify.yml").read_text()
    assert "_tautulli_host_headers" in verify


if __name__ == "__main__":
    main()
