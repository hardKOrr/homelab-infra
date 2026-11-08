#!/usr/bin/env python3
"""Check slskd's Docker-default and Kubernetes hosting contract."""

from pathlib import Path
import json

import jinja2
import yaml


REPO = Path(__file__).resolve().parents[1]
ROLE = "ansible/roles/slskd"


def read_yaml(path):
    return yaml.safe_load((REPO / path).read_text())


def includes(tasks, target):
    return any(target in str(task.get("ansible.builtin.include_tasks", "")) for task in tasks)


def render_manifest():
    env = jinja2.Environment(extensions=["jinja2.ext.do"], keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    env.filters["regex_replace"] = lambda v, a, b: __import__("re").sub(a, b, str(v))
    env.filters["hash"] = lambda v, _algo: __import__("hashlib").sha256(str(v).encode()).hexdigest()
    template = env.from_string((REPO / ROLE / "templates/manifest.yaml.j2").read_text())
    text = template.render(
        instance="slskd",
        app_config={"app": {"image": "example/slskd:1", "port": 5030, "container_port": 5030,
                            "listen_port": 50300, "puid": 1313, "pgid": 1313, "storage": 2,
                            "shared_dirs": ["/mnt/media/music"]}},
        homelabinfra_config={"timezone": "UTC"},
        _slskd_api_key="k" * 32, _slskd_web_password="w", _slskd_soulseek_username="u",
        _slskd_soulseek_password="p", slskd_fqdn="slskd.example.test",
        k8s_media_volumes=[{"name": "share-0", "nfs": {"server": "192.0.2.5", "path": "/export/media/downloads/complete", "readOnly": False}}],
        k8s_media_volume_mounts=[{"name": "share-0", "mountPath": "/downloads/complete", "readOnly": False}],
    )
    return [d for d in yaml.safe_load_all(text) if d]


def main():
    defaults = read_yaml("ansible/vars/app-defaults/slskd.yml")["slskd_defaults"]
    assert defaults["hosting"]["method"] == "docker"
    assert "kubernetes" in defaults["hosting"]["supported"]
    assert defaults["recovery"]["methods"] == ["native"]
    for key in ("image", "key_url", "repository", "suite", "component"):
        assert defaults["backup"]["client"].get(key), f"backup.client.{key} missing"

    plays = {play["name"]: play for play in read_yaml("ansible/playbooks/apps/slskd.yml")}
    docker = plays["slskd | Deploy"]
    assert any(task.get("ansible.builtin.include_role", {}).get("name") == "slskd"
               and task.get("when") == "hostvars['localhost'].app_config.hosting.method == 'docker'"
               for task in docker["tasks"])
    kube = plays["slskd | Deploy on Kubernetes"]
    assert any(task.get("ansible.builtin.include_role", {}).get("name") == "slskd"
               and task.get("when") == "app_config.hosting.method == 'kubernetes'"
               for task in kube["tasks"])
    prepare = plays["slskd | Prepare Kubernetes"]["tasks"]
    assert any(task.get("ansible.builtin.include_role", {}).get("tasks_from") == "resolve-kubernetes-media"
               for task in prepare)
    assert includes(prepare, "tasks/kubernetes/open-app-access.yml")

    resolve = read_yaml(f"{ROLE}/tasks/resolve-kubernetes-media.yml")
    order = [str(t.get("ansible.builtin.include_tasks", "")) + str(t.get("ansible.builtin.file", ""))
             for t in resolve]
    nfs = next(i for i, x in enumerate(order) if "resolve-media-nfs.yml" in x)
    made = next(i for i, x in enumerate(order) if "directory" in x)
    assert nfs < made, "NFS validation must precede directory creation"
    shares = next(t for t in resolve if "_slskd_k8s_shares" in str(t.get("ansible.builtin.set_fact")))
    text = str(shares)
    assert "/downloads/complete" in str(resolve) and "/downloads/incomplete" in str(resolve)
    assert "'mount_path': d.container" in text and "'read_only': d.read_only" in text

    kubernetes = read_yaml(f"{ROLE}/tasks/kubernetes.yml")
    assert includes(kubernetes, "tasks/kubernetes/prepare-media-volumes.yml")
    assert includes(kubernetes, "tasks/kubernetes/apply-manifest.yml")
    backup = next(t for t in kubernetes if "tasks/kubernetes/recovery-backup.yml" in str(t.get("ansible.builtin.include_tasks")))
    claim = backup["vars"]["k8s_recovery"]["archives"][0]
    assert claim["name"] == "data" and claim["claim"] == "{{ instance }}-config"

    restore = read_yaml(f"{ROLE}/tasks/restore.yml")
    assert includes(restore, "restore-kubernetes.yml")
    job = read_yaml(f"{ROLE}/tasks/restore-kubernetes.yml")[0]
    assert "tasks/kubernetes/recovery-restore.yml" in job["ansible.builtin.include_tasks"]
    assert job["vars"]["k8s_recovery"]["archives"][0]["claim"] == "{{ restore_target }}-config"

    kinds = {doc["kind"]: doc for doc in render_manifest()}
    assert set(kinds) == {"PersistentVolumeClaim", "Deployment", "Service", "Ingress"}
    pod = kinds["Deployment"]["spec"]["template"]["spec"]
    app = pod["containers"][0]
    assert {"name": "config", "mountPath": "/app"} in app["volumeMounts"]
    assert {"name": "share-0", "mountPath": "/downloads/complete", "readOnly": False} in app["volumeMounts"]
    env = {e["name"]: e for e in app["env"]}
    assert env["SLSKD_API_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "api-key"
    assert env["SLSKD_SHARED_DIR"]["value"] == "/shared/0"
    assert "value" not in env["SLSKD_SLSK_PASSWORD"]
    assert pod["securityContext"]["runAsUser"] == 1313
    ingress = kinds["Ingress"]["spec"]["rules"][0]
    assert ingress["host"] == "slskd.example.test"
    assert ingress["http"]["paths"][0]["backend"]["service"]["port"]["number"] == 5030


if __name__ == "__main__":
    main()
