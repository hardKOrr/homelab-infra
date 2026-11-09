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

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/tautulli.yml")}
    provision = plays["Tautulli | Provision"]["tasks"]
    # Docker stays the default: the stack host is only provisioned for that method.
    stack = next(t for t in provision if "find-or-create-host" in str(t))
    assert stack["when"] == "app_config.hosting.method == 'docker'"
    block = next(t for t in provision if t.get("block"))
    assert block["when"] == "app_config.hosting.method == 'kubernetes'"
    names = [str(t) for t in block["block"]]
    # Plex's endpoint is resolved before the firewall step that needs it.
    plex = next(i for i, t in enumerate(names) if "'tasks_from': 'plex'" in t)
    firewall = next(i for i, t in enumerate(names) if "open-app-access.yml" in t)
    deploy = next(i for i, t in enumerate(names) if "'tasks_from': 'kubernetes'" in t)
    assert plex < firewall < deploy
    assert next(i for i, t in enumerate(names) if "resolve-estate.yml" in t) < next(
        i for i, t in enumerate(names) if "tautulli_fqdn" in t)

    wiring = (REPO / "ansible/playbooks/apps/tautulli.yml").read_text()
    assert "ingress_vip" in wiring and "recovery_isolated" in wiring

    tasks = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert includes(tasks, "tasks/kubernetes/apply-manifest.yml")
    assert includes(tasks, "tasks/kubernetes/sync-secret.yml")
    # No media shares: Tautulli's Docker stack mounts none.
    assert not includes(tasks, "prepare-media-volumes.yml")
    order = [str(t.get("ansible.builtin.include_tasks")) for t in tasks]
    assert (next(i for i, o in enumerate(order) if "upsert-item" in o)
            < next(i for i, o in enumerate(order) if "sync-secret" in o)
            < next(i for i, o in enumerate(order) if "apply-manifest" in o))
    backup = next(t for t in tasks if "recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    archive = backup["vars"]["k8s_recovery"]["archives"][0]
    assert archive["name"] == "data" and archive["claim"] == "{{ instance }}-config"

    manifest = (REPO / f"{ROLE}/templates/manifest.yaml.j2").read_text()
    assert "secretKeyRef" in manifest and "kind: Ingress" in manifest
    assert "storageClassName" not in manifest
    assert "mountPath: /config" in manifest
    # A rotated credential must roll the pod: env from a Secret is read at start.
    assert "checksum/credentials" in manifest
    assert "_tautulli_plex_token" in manifest.split("checksum/credentials")[1].split("\n")[0]
    assert "_tautulli_api_key" in manifest.split("checksum/credentials")[1].split("\n")[0]

    restore = read_yaml(f"{ROLE}/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml") and includes(restore, "restore-guest.yml")
    job = read_yaml(f"{ROLE}/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    verify = (REPO / f"{ROLE}/tasks/verify.yml").read_text()
    assert "_tautulli_host_headers" in verify


if __name__ == "__main__":
    main()
